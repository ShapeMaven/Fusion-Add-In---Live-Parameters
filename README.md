
# Live Parameters for Fusion

Live Parameters is a dockable panel that lists all of a design's user and model parameters and lets you edit them in place. It stays open while you work, including while a sketch is being edited, so you don't need the Change Parameters dialog.

## Requirements

- Autodesk Fusion on Windows or macOS.
- No other software or Python packages.

## Installing

1. **Unzip** `LiveParameters.zip`. You need the `LiveParameters` folder, which contains:
   ```
   LiveParameters/
     LiveParameters.py
     LiveParameters.manifest
     palette.html
   ```
   Keep the folder named `LiveParameters`. Fusion requires the folder, the `.py` file and the `.manifest` file to share the same name.

2. **Put the folder somewhere permanent.** Either of these works:
   - Fusion's add-in folder (Fusion finds it automatically):
     - Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`
     - macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/`
   - Any other folder you choose. You then add it to Fusion yourself in step 3.

3. **Load it in Fusion:**
   1. Open **Utilities ▸ Add-Ins ▸ Scripts and Add-Ins**, or press **Shift + S**.
   2. If **LiveParameters** isn't in the list, click **+** and choose the `LiveParameters` folder. On older Fusion versions, click the green **+** next to *My Add-Ins* on the **Add-Ins** tab.
   3. Select **LiveParameters** and click **Run**.
   4. Tick **Run on Startup** so it loads every time Fusion starts. This should already be ticked by default.

The **Live Parameters** panel opens docked on the right.

### Updating

1. In **Scripts and Add-Ins**, select LiveParameters and click **Stop**.
2. Replace the three files with the new ones.
3. Click **Run** again.

You don't need to restart Fusion. If the panel shows a version warning, close and reopen it.

### Removing

1. Stop the add-in.
2. Untick **Run on Startup**.
3. Remove it from the list (**–** button, or right-click ▸ Remove).
4. Delete the folder.

## Opening the panel

The panel opens when the add-in starts. If you close it, reopen it with the **Live Parameters** button. The button is in:

- **Solid ▸ Modify**
- **Sketch ▸ Modify** (while editing a sketch)

You can drag the panel to undock it, or dock it on either side.

The panel follows the active document. It updates automatically:
- when you switch document tabs
- after any command, including undo/redo, edits made in Fusion's own Change Parameters dialog, and timeline renames

Use **⟳** to force a refresh.

## Using it

### Toolbar

| Control | What it does |
|---|---|
| **Filter…** | Shows only parameters whose name, expression or comment matches the text |
| **★ only** | Shows only favourite parameters |
| **Export** | Saves every parameter to a CSV file (see below) |
| **⟳** | Refreshes the list |

### Editing parameters

- **Name, Expression and Comment** can be edited in place. Click a cell, type, then press **Enter** or click away to apply it.
- **Value** shows the evaluated result.
- If Fusion rejects an edit, the panel shows the reason and keeps the old value.
- **☆ / ★** marks a parameter as a favourite.
- The delete button at the end of a row removes a user parameter after you confirm. Fusion won't delete parameters that are still in use.
- **Drag a column header's edge** to resize that column. Double-click the edge to reset all column widths.

### Adding a user parameter

1. Expand **+ Add user parameter**.
2. Enter a name, expression, unit (default `mm`) and an optional comment.
3. Optionally choose a group.
4. Click **Add**.

### Expression calculator (ƒx)

Click **ƒx** next to an expression field to open the calculator. It provides:

- **A live result.** The result updates as you type. A wrong unit is reported separately from a syntax error, for example "The result is an angle, but this parameter needs mm."
- **A formatted view.** Click part of the expression to select it.
- **Tabs** for inserting values:
  - **Keypad**
  - **Functions** (sin, sqrt, round …)
  - **Parameters** (every parameter in the design)
  - **Units**
- **Tidy up**, which rewrites the expression without unnecessary brackets and spacing. Fusion checks the rewrite before it's offered.
- **Ctrl+Z / Ctrl+Y** for undo/redo, **Enter** to apply and **Esc** to cancel.

A parameter can't refer to itself, and the calculator flags this before applying.

### Organising user parameters into groups

User parameters can be sorted into named groups and put in any order. Groups and order are saved inside the design, so they travel with the file.

**Selecting parameters:**
- Click a row to select it.
- **Ctrl/Cmd+click** adds or removes a row from the selection.
- **Shift+click** selects a range.

**With rows selected,** the selection bar lets you:
- move them to a group
- make a **New group** from them
- **Ungroup** them
- **✕** clear the selection

**Dragging:**
- Drag a row by its grip to reorder it or drop it into another group.
- Drag a group header to reorder groups.

**Right-click a row** for the same actions:
- move to a group
- new group from selection
- remove from group
- clear selection

**Group headers:**
- Click to collapse or expand.
- Double-click to rename.

Parameters not in any group are listed under **Ungrouped**.

### Model parameters

Model parameters, such as sketch dimensions and feature distances, are listed:
1. by component
2. then by the sketch or feature that owns them
3. in timeline order

Each parameter can be edited the same way as a user parameter. Double-click a feature or sketch header to rename that feature in the timeline.

### CSV export

**Export** asks where to save, then writes one row per parameter. The file is UTF-8 so Excel shows degree signs and other symbols correctly.

The columns are:
- Type (User/Model)
- Group or Component
- Feature
- Timeline #
- Name
- Expression
- Value
- Numeric Value
- Unit
- Role
- Comment
- Favourite

User parameters come first, in group and display order. Model parameters follow, in component and timeline order.

## Troubleshooting

- **No button or panel after Run:**
  1. Check that the folder, `.py` and `.manifest` names all match (`LiveParameters`).
  2. Check that `palette.html` is in the same folder.
  3. Stop and Run the add-in again.
- **The panel is blank or shows a version warning:** the page and the Python file come from different versions. Replace all three files, then Stop and Run.
- **"Live Parameters failed to start" message:** it contains the error details. Send them along when reporting a problem.
- **The list doesn't match the open design:** click **⟳**.
