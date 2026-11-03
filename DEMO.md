# Ten-minute offline demonstration

All examples below are synthetic. Explain the software workflow without presenting the values as titanium data.

1. Start the local app. Click **Load area practice** and select **Micrograph**. Enter a sample label such as `DEMO-SAMPLE` and a source such as `Built-in synthetic area practice`.
2. Click **Set scale-bar endpoints**, then the centers of the two ticks on the lower-left bar. Enter 10 µm. The intended calibration is 100 source pixels = 10 µm.
3. Choose **Area**, trace the square and finish the region. The mathematical square has corners (200,200), (400,200), (400,400), (200,400); its area is 400 µm² and ECD is 22.5676 µm. Manual clicks and raster strokes can differ slightly. ECD means a circle with the same 2D area, not a phase assignment.
4. Select the region, drag a handle, then undo and redo. Undo back to an already saved state clears the unsaved marker. New projects remain unsaved until saved.
5. Enter a review note. Mark geometry reviewed only after checking it. Save, reopen and export CSV. Check the displayed D-drive path. Optional object summaries need an object group, explicit inclusion and current review.
6. Load **graph practice**. Calibrate X using 10 at (100,600) and 90 at (900,600), unit degrees. The synthetic peaks are at 35, 50 and 70 degrees. Use **Point** or inspect **Peak Assist** candidates; their Y positions are not normalized XRD intensity.
7. For two-axis practice enable Y: 0 at (100,600), 100 at (100,100). Point (500,300) means X=50 and Y=60. Save and reopen to show coordinate provenance.
8. Import `examples/synthetic-reference.json` to inspect existing targets and sample identifiers. Mapping requires compatible definitions and units plus separate review. A single square must not be mapped directly to a population mean.
9. Click **Export TiFM copy** and export an unchanged copy with attachments. Explain that no result is selected automatically. A reviewed exact experimental author report or eligible geometry mapping is needed for numerical replacement; this synthetic reference is not an experimental dataset.

The automated `scripts.smoke_release` check uses exact source coordinates for these examples. It verifies arithmetic/storage without depending on manual click precision. It does not test arbitrary papers or the downstream transformer.
