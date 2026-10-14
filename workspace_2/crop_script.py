from PIL import Image

im = Image.open(r'C:\Users\admin\.gemini\antigravity\brain\bc4fc2da-bc3a-4bdf-a33e-b015c44e82b3\.user_uploaded\media_1791762765829_9834cbb4.png')
w, h = im.size

# Bottom boundaries found earlier:
# [83, 138, 205, 295, 338, 397, 479, 519, 609, 664, 719]
# Let's crop each cell from bottom row (y=28 to 58)
# Bounds from left to right:
# Cell 0: 0 to 83
# Cell 1: 83 to 138
# Cell 2: 138 to 205
# Cell 3: 205 to 295
# Cell 4: 295 to 338
# Cell 5: 338 to 397
# Cell 6: 397 to 479
# Cell 7: 479 to 519
# Cell 8: 519 to 609
# Cell 9: 609 to 664
# Cell 10: 664 to 719

bounds = [0, 83, 138, 205, 295, 338, 397, 479, 519, 609, 664, 719]

import os
os.makedirs("workspace_2/crop_cells", exist_ok=True)
for i in range(len(bounds)-1):
    x1, x2 = bounds[i], bounds[i+1]
    cropped = im.crop((x1, 28, x2, 58))
    cropped.save(f"workspace_2/crop_cells/cell_{i}_{x1}_{x2}.png")

print("Saved cropped cells to workspace_2/crop_cells/")
