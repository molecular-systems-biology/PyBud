import java.awt.Robot
import java.awt.event.InputEvent
from javax.swing import JFileChooser
from ij import IJ, WindowManager
from ij.plugin.frame import RoiManager
import java.lang.Thread

IJ.showMessage("This script generates clicks at the positions used in PyBud.\nMake sure you have opened the same measurement and activated BudJ.\nIn the next dialog, you can select the ROIs exported from PyBud.");

# Show file chooser to select a ZIP file
chooser = JFileChooser()
chooser.setDialogTitle("Select ROI ZIP File")
chooser.setFileSelectionMode(JFileChooser.FILES_ONLY)

if chooser.showOpenDialog(None) == JFileChooser.APPROVE_OPTION:
    roi_zip_path = chooser.getSelectedFile().getAbsolutePath()
else:
    print("No file selected.")
    raise SystemExit

# Load the current image
imp = IJ.getImage()
if imp is None:
    print("No image open.")
    raise SystemExit

# Initialize or get the existing RoiManager
rm = RoiManager.getInstance()
if rm is None:
    rm = RoiManager()
else:
    rm.reset()

# Open the ROI zip file into RoiManager
rm.runCommand("Open", roi_zip_path)

# Get image canvas and screen position
win = imp.getWindow()
canvas = win.getCanvas()
screen_loc = canvas.getLocationOnScreen()

# Create Robot instance
robot = java.awt.Robot()

# Track the current frame to avoid redundant setT calls
current_frame = -1

# Loop over ROIs and click only on point ROIs
for i in range(rm.getCount()):
    roi = rm.getRoi(i)
    if roi.getTypeAsString() == "Point":
        frame = roi.getTPosition()  # 1-based frame index
        if frame > 0 and frame != current_frame:
            #imp.setT(frame)
            imp.setPosition(frame)
            current_frame = frame
            java.lang.Thread.sleep(300)  # Allow time for display update

        points = roi.getContainedPoints()
        for pt in points:
            screen_x = screen_loc.x + canvas.screenX(pt.x)
            screen_y = screen_loc.y + canvas.screenY(pt.y)

            # Perform mouse click
            robot.mouseMove(screen_x, screen_y)
            robot.mousePress(java.awt.event.InputEvent.BUTTON1_DOWN_MASK)
            robot.mouseRelease(java.awt.event.InputEvent.BUTTON1_DOWN_MASK)

            # Optional: wait briefly between clicks
            java.lang.Thread.sleep(500)