"""
fiji_replay_clicks.py — Fiji/ImageJ Jython script for BudJ comparison.

PURPOSE
-------
This script runs inside the Fiji/ImageJ Jython interpreter (NOT standard
CPython). It automates mouse-click replay in BudJ so you can reproduce
PyBud's seed positions exactly in BudJ for a side-by-side comparison.

HOW TO USE
----------
1. Open the same TIF in Fiji and start BudJ.
2. In Fiji: Plugins → Macros → Run... → select this file.
3. In the file chooser that opens, select an ROI ZIP exported from PyBud
   (File menu → Export ROIs in the PyBud GUI).
4. The script reads the point ROIs and simulates clicks at each position
   on the correct frame, reproducing the PyBud seed placement in BudJ.

DEPENDENCIES
------------
Requires Fiji (https://fiji.sc) with BudJ installed.
Java classes (java.awt.Robot, ij.*, etc.) are provided by the Fiji JVM.
This file cannot be executed with the standard ``python`` command.
"""

import java.awt.Robot
import java.awt.event.InputEvent
from javax.swing import JFileChooser
from ij import IJ, WindowManager
from ij.plugin.frame import RoiManager
import java.lang.Thread

IJ.showMessage(
    "This script replays PyBud seed clicks in BudJ.\n"
    "Make sure you have opened the same image and activated BudJ.\n"
    "In the next dialog, select the ROI ZIP exported from PyBud."
)

# File chooser for the ROI ZIP
chooser = JFileChooser()
chooser.setDialogTitle("Select ROI ZIP File")
chooser.setFileSelectionMode(JFileChooser.FILES_ONLY)

if chooser.showOpenDialog(None) == JFileChooser.APPROVE_OPTION:
    roi_zip_path = chooser.getSelectedFile().getAbsolutePath()
else:
    print("No file selected.")
    raise SystemExit

# Verify an image is open
imp = IJ.getImage()
if imp is None:
    print("No image open.")
    raise SystemExit

# Load ROIs into the ROI Manager
rm = RoiManager.getInstance()
if rm is None:
    rm = RoiManager()
else:
    rm.reset()
rm.runCommand("Open", roi_zip_path)

# Get the image canvas screen position for coordinate mapping
win    = imp.getWindow()
canvas = win.getCanvas()
screen_loc = canvas.getLocationOnScreen()

robot         = java.awt.Robot()
current_frame = -1

for i in range(rm.getCount()):
    roi = rm.getRoi(i)
    if roi.getTypeAsString() != "Point":
        continue

    frame = roi.getTPosition()   # 1-based
    if frame > 0 and frame != current_frame:
        imp.setPosition(frame)
        current_frame = frame
        java.lang.Thread.sleep(300)   # let the display update

    for pt in roi.getContainedPoints():
        screen_x = screen_loc.x + canvas.screenX(pt.x)
        screen_y = screen_loc.y + canvas.screenY(pt.y)

        robot.mouseMove(screen_x, screen_y)
        robot.mousePress(java.awt.event.InputEvent.BUTTON1_DOWN_MASK)
        robot.mouseRelease(java.awt.event.InputEvent.BUTTON1_DOWN_MASK)
        java.lang.Thread.sleep(500)
