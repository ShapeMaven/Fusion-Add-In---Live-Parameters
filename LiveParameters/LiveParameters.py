"""
Live Parameters - Fusion add-in
A dockable palette that lists user + model parameters and lets you edit them
in place, including while a sketch is being edited.

- User parameters can be organised into named groups and freely reordered.
  Groups and order are stored as an attribute on the Design, so they are saved
  with the document.
- Model parameters are shown by component, then by the feature/sketch that owns
  them, in timeline order. Features can be renamed from the palette.
- Everything can be exported to CSV.
"""

import adsk.core
import adsk.fusion
import traceback
import json
import os
import csv
import re
import threading

APP_NAME = 'Live Parameters'
VERSION = '1.5'   # must match PAGE_VERSION in palette.html
CMD_ID = 'liveParams_showPalette'
PALETTE_ID = 'liveParams_palette'
PANEL_IDS = ['SolidModifyPanel', 'SketchModifyPanel']

ATTR_GROUP = 'LiveParameters'
ATTR_NAME = 'groups'
REFRESH_EVENT_ID = 'liveParams_refresh'
POLL_SECONDS = 1.0

_app = None
_ui = None
_handlers = []
_last_doc_key = None      # document last sent to the palette
_watcher = None
_watch_stop = None
_refresh_event = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _design(doc=None):
    """Return the Design of the given document (default: the active document).

    Reads it from the document itself rather than app.activeProduct, which can
    still point at the previous file while a tab switch is in progress."""
    try:
        doc = doc or _app.activeDocument
        if doc:
            d = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
            if d:
                return d
        if doc is None:
            return adsk.fusion.Design.cast(_app.activeProduct)
    except:
        pass
    return None


def _doc_key(doc):
    """Stable identifier for a document (names are not unique, e.g. two 'Untitled')."""
    if not doc:
        return None
    for fn in (lambda: doc.creationId, lambda: doc.dataFile.id):
        k = _safe(fn, '')
        if k:
            return str(k)
    return 'name:' + _safe(lambda: doc.name, '')


def _safe(fn, default=''):
    try:
        v = fn()
        return default if v is None else v
    except:
        return default


def _fmt_value(design, p, with_units=True):
    try:
        if p.unit:
            return design.unitsManager.formatInternalValue(p.value, p.unit, with_units)
        return '{:g}'.format(p.value)
    except:
        return ''


def _param_dict(design, p):
    return {
        'name': p.name,
        'expr': _safe(lambda: p.expression),
        'value': _fmt_value(design, p),
        'unit': _safe(lambda: p.unit),
        'comment': _safe(lambda: p.comment),
        'fav': bool(_safe(lambda: p.isFavorite, False)),
    }


def _find(design, name):
    p = design.allParameters.itemByName(name)
    if not p:
        raise ValueError('Parameter "{}" not found.'.format(name))
    return p


def _user_names(design, names):
    """Keep only names that are existing user parameters, preserving order."""
    out = []
    for n in names or []:
        n = str(n)
        if n not in out and design.userParameters.itemByName(n):
            out.append(n)
    return out


def _timeline_key(entity):
    """Sortable timeline position of a feature/sketch/plane, or None if it has none.

    Works whether TimelineObject.index is global or relative to its group."""
    try:
        to = entity.timelineObject
    except:
        return None
    if not to:
        return None
    idx = _safe(lambda: to.index, None)
    if idx is None:
        return None
    grp = _safe(lambda: to.parentGroup, None)
    if grp:
        gidx = _safe(lambda: grp.index, None)
        if gidx is not None:
            return (gidx, idx)
    return (idx, 0)


# --------------------------------------------------------------------------
# Group / order storage (design attribute holding JSON)
#
#   {"v": 2, "groups": [{"name": str, "params": [str]}], "ungrouped": [str]}
#
# "ungrouped" holds the custom order of user parameters not in any group.
# --------------------------------------------------------------------------

def _load_state(design):
    raw = {}
    try:
        a = design.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
        if a and a.value:
            raw = json.loads(a.value) or {}
    except:
        raw = {}

    groups, seen_groups, seen_params = [], set(), set()
    for g in raw.get('groups', []):
        if not isinstance(g, dict):
            continue
        name = str(g.get('name', '')).strip()
        if not name or name in seen_groups:
            continue
        seen_groups.add(name)
        params = []
        for p in g.get('params', []):
            p = str(p)
            if p and p not in seen_params:
                seen_params.add(p)
                params.append(p)
        groups.append({'name': name, 'params': params})

    ungrouped = []
    for p in raw.get('ungrouped', []):
        p = str(p)
        if p and p not in seen_params:
            seen_params.add(p)
            ungrouped.append(p)

    return {'groups': groups, 'ungrouped': ungrouped}


def _save_state(design, state):
    value = json.dumps({'v': 2, 'groups': state['groups'], 'ungrouped': state['ungrouped']})
    a = design.attributes.itemByName(ATTR_GROUP, ATTR_NAME)
    if a:
        a.value = value
    else:
        a = design.attributes.add(ATTR_GROUP, ATTR_NAME, value)
        if not a:
            raise RuntimeError('Fusion refused to store the parameter groups on this design.')


def _group_by_name(state, name):
    for g in state['groups']:
        if g['name'] == name:
            return g
    return None


def _unique_group_name(state, base):
    existing = {g['name'] for g in state['groups']}
    if base not in existing:
        return base
    i = 2
    while '{} {}'.format(base, i) in existing:
        i += 1
    return '{} {}'.format(base, i)


def _detach(state, names):
    names = set(names)
    for g in state['groups']:
        g['params'] = [p for p in g['params'] if p not in names]
    state['ungrouped'] = [p for p in state['ungrouped'] if p not in names]


def _full_ungrouped(design, state):
    """Make state['ungrouped'] list every current ungrouped user parameter, in display order."""
    names = [p.name for p in design.userParameters]
    grouped = {n for g in state['groups'] for n in g['params']}
    current = set(names)
    order = [n for n in state['ungrouped'] if n in current and n not in grouped]
    seen = set(order)
    order += [n for n in names if n not in grouped and n not in seen]
    state['ungrouped'] = order
    return order


def _insert(lst, items, anchor, where):
    if anchor and anchor in lst:
        i = lst.index(anchor) + (1 if where == 'after' else 0)
        lst[i:i] = items
    else:
        lst.extend(items)


def _rename_in_state(state, old, new):
    changed = False
    for lst in [g['params'] for g in state['groups']] + [state['ungrouped']]:
        if old in lst:
            lst[lst.index(old)] = new
            changed = True
    return changed


# --------------------------------------------------------------------------
# Data collection
# --------------------------------------------------------------------------

def _collect(doc=None):
    doc = doc or _safe(lambda: _app.activeDocument, None)
    design = _design(doc)
    if not design:
        return {'version': VERSION, 'doc': None, 'docKey': _doc_key(doc), 'user': [], 'model': [], 'groups': [], 'ungrouped': []}

    user = [_param_dict(design, p) for p in design.userParameters]

    # Model parameters with their owner and timeline position.
    model = []
    n = 0
    for ci, comp in enumerate(design.allComponents):
        comp_name = _safe(lambda: comp.name, 'Component')
        for mp in comp.modelParameters:
            owner = _safe(lambda: mp.createdBy, None)
            tl = _timeline_key(owner) if owner else None
            owner_name = _safe(lambda: owner.name) if owner else ''
            d = _param_dict(design, mp)
            d.update({
                'comp': comp_name,
                'compId': ci,
                'owner': owner_name or '(other)',
                'ownerKey': '{}.{}'.format(*tl) if tl else 'n:' + (owner_name or '(other)'),
                'tl': tl,
                'role': _safe(lambda: mp.role),
                '_seq': n,
            })
            n += 1
            model.append(d)

    # Order: components by their first timeline item, then features by timeline
    # position. Items with no timeline (direct modelling) keep Fusion's order.
    BIG = (10 ** 9, 0)
    comp_first = {}
    for d in model:
        k = tuple(d['tl']) if d['tl'] else BIG
        if d['compId'] not in comp_first or k < comp_first[d['compId']]:
            comp_first[d['compId']] = k
    model.sort(key=lambda d: (comp_first[d['compId']], d['compId'],
                              tuple(d['tl']) if d['tl'] else BIG, d['_seq']))
    for d in model:
        del d['_seq']
        d['tl'] = list(d['tl']) if d['tl'] else None

    # Groups and ungrouped order (only names that are still user parameters).
    state = _load_state(design)
    user_names = {p['name'] for p in user}
    groups = [{'name': g['name'], 'params': [x for x in g['params'] if x in user_names]}
              for g in state['groups']]
    ungrouped = _full_ungrouped(design, state)

    return {
        'version': VERSION,
        'doc': _safe(lambda: doc.name, 'Untitled'),
        'docKey': _doc_key(doc),
        'user': user,
        'model': model,
        'groups': groups,
        'ungrouped': ungrouped,
    }


def _push_data(doc=None):
    """Send fresh data to the palette if it is open."""
    global _last_doc_key
    try:
        pal = _ui.palettes.itemById(PALETTE_ID)
        if pal and pal.isVisible:
            data = _collect(doc)
            pal.sendInfoToHTML('data', json.dumps(data))
            _last_doc_key = data['docKey']
    except:
        pass


def _request_refresh(reason='force'):
    """Queue a refresh on Fusion's main thread (runs after the current event finishes)."""
    try:
        _app.fireCustomEvent(REFRESH_EVENT_ID, reason)
    except:
        pass


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def _export_csv(design):
    data = _collect()
    doc_name = data['doc'] or 'Design'

    dlg = _ui.createFileDialog()
    dlg.title = 'Export parameters'
    dlg.filter = 'CSV file (*.csv)'
    dlg.initialFilename = '{} parameters.csv'.format(doc_name)
    if dlg.showSave() != adsk.core.DialogResults.DialogOK:
        return None
    path = dlg.filename
    if not path.lower().endswith('.csv'):
        path += '.csv'

    by_name = {p.name: p for p in design.userParameters}
    user_info = {p['name']: p for p in data['user']}

    header = ['Type', 'Group / Component', 'Feature', 'Timeline #', 'Name', 'Expression',
              'Value', 'Numeric Value', 'Unit', 'Role', 'Comment', 'Favourite']
    rows = []

    def numeric(p):
        return _fmt_value(design, p, False)

    # User parameters: groups in order, then ungrouped, in the palette's order.
    for g in data['groups']:
        for n in g['params']:
            u = user_info.get(n)
            if u:
                rows.append(['User', g['name'], '', '', n, u['expr'], u['value'], numeric(by_name[n]),
                             u['unit'], '', u['comment'], 'Yes' if u['fav'] else ''])
    for n in data['ungrouped']:
        u = user_info.get(n)
        if u:
            rows.append(['User', '', '', '', n, u['expr'], u['value'], numeric(by_name[n]),
                         u['unit'], '', u['comment'], 'Yes' if u['fav'] else ''])

    # Model parameters: already sorted by component then timeline.
    tl_numbers = {}
    for m in data['model']:
        if m['tl'] is not None and m['ownerKey'] not in tl_numbers:
            tl_numbers[m['ownerKey']] = len(tl_numbers) + 1
    for m in data['model']:
        p = design.allParameters.itemByName(m['name'])
        rows.append(['Model', m['comp'], m['owner'], tl_numbers.get(m['ownerKey'], ''), m['name'],
                     m['expr'], m['value'], numeric(p) if p else '', m['unit'], m['role'],
                     m['comment'], 'Yes' if m['fav'] else ''])

    # utf-8-sig so Excel picks up the encoding (degree signs, etc.)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path, len(rows)


# --------------------------------------------------------------------------
# Expression preview (calculator)
# --------------------------------------------------------------------------

_KIND_PROBES = [('mm', 'a length'), ('deg', 'an angle'), ('', 'unitless'), ('mm^2', 'an area'),
                ('mm^3', 'a volume'), ('kg', 'a mass'), ('s', 'a time')]


def _evaluate(design, payload):
    um = design.unitsManager
    expr = (payload.get('expression') or '').strip()
    name = payload.get('name') or ''
    p = design.allParameters.itemByName(name) if name else None
    unit = payload['unit'] if 'unit' in payload else (p.unit if p else '')
    if not expr:
        return {'ok': True, 'valid': False, 'error': 'Enter an expression.'}
    if name and re.search(r'(?<![A-Za-z0-9_"$°µ])' + re.escape(name) + r'(?![A-Za-z0-9_"$°µ])', expr):
        return {'ok': True, 'valid': False, 'error': 'A parameter cannot refer to itself.'}
    try:
        valid = um.isValidExpression(expr, unit)
    except Exception:
        valid = False
    if not valid:
        # Work out whether it is a units problem rather than a syntax problem.
        for probe, label in _KIND_PROBES:
            if probe == unit:
                continue
            try:
                if um.isValidExpression(expr, probe):
                    want = 'unitless' if not unit else unit
                    return {'ok': True, 'valid': False,
                            'error': 'The result is {}, but this parameter needs {}.'.format(label, want)}
            except Exception:
                pass
        return {'ok': True, 'valid': False, 'error': 'Fusion cannot evaluate this expression.'}
    try:
        v = um.evaluateExpression(expr, unit)
        shown = um.formatInternalValue(v, unit, True) if unit else '{:g}'.format(v)
    except Exception as e:
        return {'ok': True, 'valid': False, 'error': str(e) or 'Fusion cannot evaluate this expression.'}
    return {'ok': True, 'valid': True, 'value': shown, 'raw': v}


# --------------------------------------------------------------------------
# Actions from the palette
# --------------------------------------------------------------------------

def _dispatch(action, payload):
    if action == 'refresh':
        return {'ok': True, 'data': _collect()}

    design = _design()
    if not design:
        return {'ok': False, 'error': 'No active design.'}

    um = design.unitsManager
    extra = {}

    # ---- parameter actions ------------------------------------------------

    if action == 'setExpression':
        p = _find(design, payload['name'])
        expr = payload['expression'].strip()
        if not um.isValidExpression(expr, p.unit):
            return {'ok': False, 'error': 'Invalid expression for unit "{}": {}'.format(p.unit or 'none', expr)}
        p.expression = expr

    elif action == 'rename':
        p = _find(design, payload['name'])
        old_name = p.name
        new_name = payload['newName'].strip()
        if not new_name:
            return {'ok': False, 'error': 'Name cannot be empty.'}
        if new_name != old_name:
            p.name = new_name
            state = _load_state(design)
            if _rename_in_state(state, old_name, new_name):
                _save_state(design, state)

    elif action == 'setComment':
        p = _find(design, payload['name'])
        p.comment = payload['comment']

    elif action == 'toggleFavorite':
        p = _find(design, payload['name'])
        p.isFavorite = not p.isFavorite

    elif action == 'add':
        name = payload['name'].strip()
        expr = payload['expression'].strip()
        unit = payload.get('unit', '').strip()
        comment = payload.get('comment', '')
        group = payload.get('group', '').strip()
        if not name or not expr:
            return {'ok': False, 'error': 'Name and expression are required.'}
        if design.allParameters.itemByName(name):
            return {'ok': False, 'error': 'A parameter named "{}" already exists.'.format(name)}
        if not um.isValidExpression(expr, unit):
            return {'ok': False, 'error': 'Invalid expression for unit "{}": {}'.format(unit or 'none', expr)}
        design.userParameters.add(name, adsk.core.ValueInput.createByString(expr), unit, comment)
        state = _load_state(design)
        g = _group_by_name(state, group) if group else None
        if g:
            _detach(state, [name])
            g['params'].append(name)
            _save_state(design, state)

    elif action == 'delete':
        p = design.userParameters.itemByName(payload['name'])
        if not p:
            return {'ok': False, 'error': 'Only user parameters can be deleted.'}
        name = p.name
        if not p.deleteMe():
            return {'ok': False, 'error': 'Could not delete "{}" (it is probably referenced).'.format(name)}
        state = _load_state(design)
        _detach(state, [name])
        _save_state(design, state)

    elif action == 'renameFeature':
        # Rename the feature/sketch/plane that owns a model parameter (same as
        # renaming it in the timeline).
        p = _find(design, payload['param'])
        owner = _safe(lambda: p.createdBy, None)
        if not owner:
            return {'ok': False, 'error': 'Could not find the feature that owns "{}".'.format(p.name)}
        new_name = payload['newName'].strip()
        if not new_name:
            return {'ok': False, 'error': 'Name cannot be empty.'}
        try:
            owner.name = new_name
        except Exception as e:
            return {'ok': False, 'error': 'Fusion would not rename "{}": {}'.format(_safe(lambda: owner.name), e)}

    elif action == 'evaluate':
        # Preview an expression without changing anything (used by the calculator).
        return _evaluate(design, payload)

    elif action == 'export':
        result = _export_csv(design)
        if result is None:
            return {'ok': True, 'cancelled': True}
        path, count = result
        return {'ok': True, 'notice': 'Exported {} parameters to {}'.format(count, path)}

    # ---- group / order actions (user parameters only) ---------------------

    elif action == 'groupCreate':
        state = _load_state(design)
        _full_ungrouped(design, state)
        name = payload.get('name', '').strip()
        if name and _group_by_name(state, name):
            return {'ok': False, 'error': 'A group named "{}" already exists.'.format(name)}
        if not name:
            name = _unique_group_name(state, 'New Group')
        params = _user_names(design, payload.get('params', []))
        _detach(state, params)
        state['groups'].append({'name': name, 'params': params})
        _save_state(design, state)
        extra['created'] = name

    elif action == 'groupRename':
        state = _load_state(design)
        g = _group_by_name(state, payload['name'])
        if not g:
            return {'ok': False, 'error': 'Group "{}" not found.'.format(payload['name'])}
        new_name = payload['newName'].strip()
        if not new_name:
            return {'ok': False, 'error': 'Group name cannot be empty.'}
        if new_name != g['name']:
            if _group_by_name(state, new_name):
                return {'ok': False, 'error': 'A group named "{}" already exists.'.format(new_name)}
            g['name'] = new_name
            _save_state(design, state)

    elif action == 'groupDelete':
        state = _load_state(design)
        _full_ungrouped(design, state)
        g = _group_by_name(state, payload['name'])
        if g:
            state['groups'].remove(g)
            # Released parameters go to the top of the ungrouped list, in group order.
            state['ungrouped'] = g['params'] + [n for n in state['ungrouped'] if n not in g['params']]
            _save_state(design, state)

    elif action == 'groupMove':
        # Either step by 'dir' (-1/+1) or place relative to 'target' ('before'/'after').
        state = _load_state(design)
        groups = state['groups']
        idx = next((i for i, g in enumerate(groups) if g['name'] == payload['name']), -1)
        if idx < 0:
            return {'ok': False, 'error': 'Group "{}" not found.'.format(payload['name'])}
        g = groups.pop(idx)
        target = payload.get('target')
        if target is not None:
            if target == '':                      # end of list
                groups.append(g)
            else:
                t = next((i for i, x in enumerate(groups) if x['name'] == target), None)
                if t is None:
                    groups.insert(idx, g)
                else:
                    groups.insert(t + (1 if payload.get('where') == 'after' else 0), g)
        else:
            new_idx = max(0, min(len(groups), idx + int(payload.get('dir', 0))))
            groups.insert(new_idx, g)
        _save_state(design, state)

    elif action in ('move', 'assign'):
        # Move user params into a group ('' = ungrouped list), optionally placed
        # before/after an anchor parameter in that list; otherwise appended.
        state = _load_state(design)
        _full_ungrouped(design, state)
        params = _user_names(design, payload.get('params', []))
        if not params:
            return {'ok': False, 'error': 'Only user parameters can be grouped or reordered.'}
        target_name = payload.get('group', '')
        anchor = payload.get('anchor', payload.get('before'))
        where = payload.get('where', 'before')
        if anchor in params:
            # Dropping a selection onto one of its own members: keep position by
            # anchoring on the nearest neighbour that is not being moved.
            anchor = None
        g = _group_by_name(state, target_name) if target_name else None
        if target_name and not g:
            return {'ok': False, 'error': 'Group "{}" not found.'.format(target_name)}
        _detach(state, params)   # replaces the lists, so pick the target afterwards
        _insert(g['params'] if g else state['ungrouped'], params, anchor, where)
        _save_state(design, state)

    else:
        return {'ok': False, 'error': 'Unknown action "{}".'.format(action)}

    res = {'ok': True, 'data': _collect()}
    res.update(extra)
    return res


# --------------------------------------------------------------------------
# Event handlers
# --------------------------------------------------------------------------

class HTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        a = adsk.core.HTMLEventArgs.cast(args)
        try:
            # Fusion also routes its own housekeeping messages here; ignore them.
            if not a.action or a.action == 'response':
                return
            payload = json.loads(a.data) if a.data else {}
            a.returnData = json.dumps(_dispatch(a.action, payload))
        except Exception as e:
            a.returnData = json.dumps({'ok': False, 'error': str(e) or traceback.format_exc()})


class ShowExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            pal = _get_palette()
            pal.isVisible = True
            _push_data()
        except:
            _ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


class ShowCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
        cmd.isRepeatable = False
        h = ShowExecuteHandler()
        cmd.execute.add(h)
        _handlers.append(h)


class CommandTerminatedHandler(adsk.core.ApplicationCommandEventHandler):
    """Refresh after any command finishes (catches edits from the native dialog, timeline renames, undo, etc.)."""
    def notify(self, args):
        try:
            a = adsk.core.ApplicationCommandEventArgs.cast(args)
            if a.commandId != CMD_ID:
                _push_data()
        except:
            pass


class DocActivatedHandler(adsk.core.DocumentEventHandler):
    """Fires on opening a file and on switching file tabs."""
    def notify(self, args):
        try:
            doc = adsk.core.DocumentEventArgs.cast(args).document
        except:
            doc = None
        _push_data(doc)            # immediately, using the event's own document
        _request_refresh('force')  # and again once Fusion has finished switching


class DocClosedHandler(adsk.core.DocumentEventHandler):
    def notify(self, args):
        _request_refresh('force')


class RefreshEventHandler(adsk.core.CustomEventHandler):
    """Runs on the main thread. 'poll' only refreshes if the active document changed."""
    def notify(self, args):
        try:
            info = adsk.core.CustomEventArgs.cast(args).additionalInfo
            if info == 'poll':
                if _doc_key(_safe(lambda: _app.activeDocument, None)) == _last_doc_key:
                    return
            _push_data()
        except:
            pass


class _Watcher(threading.Thread):
    """Safety net: every POLL_SECONDS ask the main thread to check for a document switch
    that the events above might have missed. The check itself is a cheap id comparison."""
    def __init__(self, stop_event):
        threading.Thread.__init__(self, daemon=True)
        self._stop_event = stop_event

    def run(self):
        while not self._stop_event.wait(POLL_SECONDS):
            _request_refresh('poll')


# --------------------------------------------------------------------------
# Palette / UI setup
# --------------------------------------------------------------------------

def _get_palette():
    pal = _ui.palettes.itemById(PALETTE_ID)
    if not pal:
        html = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'palette.html').replace('\\', '/')
        pal = _ui.palettes.add(PALETTE_ID, APP_NAME, html, True, True, True, 460, 650, True)
        pal.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
        h = HTMLHandler()
        pal.incomingFromHTML.add(h)
        _handlers.append(h)
    return pal


def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        # A palette left over from a previous version in this session would keep
        # the old page and handlers, so always start from a fresh one.
        old = _ui.palettes.itemById(PALETTE_ID)
        if old:
            old.deleteMe()

        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if not cmd_def:
            cmd_def = _ui.commandDefinitions.addButtonDefinition(
                CMD_ID, APP_NAME, 'Show the Live Parameters palette', '')
        h = ShowCreatedHandler()
        cmd_def.commandCreated.add(h)
        _handlers.append(h)

        for pid in PANEL_IDS:
            panel = _ui.allToolbarPanels.itemById(pid)
            if panel and not panel.controls.itemById(CMD_ID):
                panel.controls.addCommand(cmd_def)

        h = CommandTerminatedHandler()
        _ui.commandTerminated.add(h)
        _handlers.append(h)

        h = DocActivatedHandler()
        _app.documentActivated.add(h)
        _handlers.append(h)

        h = DocClosedHandler()
        _app.documentClosed.add(h)
        _handlers.append(h)

        global _refresh_event, _watcher, _watch_stop
        try:
            _app.unregisterCustomEvent(REFRESH_EVENT_ID)
        except:
            pass
        _refresh_event = _app.registerCustomEvent(REFRESH_EVENT_ID)
        h = RefreshEventHandler()
        _refresh_event.add(h)
        _handlers.append(h)

        _watch_stop = threading.Event()
        _watcher = _Watcher(_watch_stop)
        _watcher.start()

        _get_palette().isVisible = True
    except:
        if _ui:
            _ui.messageBox('Live Parameters failed to start:\n{}'.format(traceback.format_exc()))


def stop(context):
    global _watcher, _refresh_event, _last_doc_key
    try:
        if _watch_stop:
            _watch_stop.set()
        _watcher = None
        try:
            _app.unregisterCustomEvent(REFRESH_EVENT_ID)
        except:
            pass
        _refresh_event = None
        _last_doc_key = None

        pal = _ui.palettes.itemById(PALETTE_ID)
        if pal:
            pal.deleteMe()

        for pid in PANEL_IDS:
            panel = _ui.allToolbarPanels.itemById(pid)
            if panel:
                ctrl = panel.controls.itemById(CMD_ID)
                if ctrl:
                    ctrl.deleteMe()

        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()

        _handlers.clear()
    except:
        if _ui:
            _ui.messageBox('Live Parameters failed to stop:\n{}'.format(traceback.format_exc()))
