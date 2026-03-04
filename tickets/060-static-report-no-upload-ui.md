# 060: Static Report Must Not Include Upload UI
**Status:** OPEN  
**Priority:** P1

## Description
Generated static HTML reports should present only the analyzed tour output.

They should not include the upload form, upload prompt, "Analyze Another" button, or other upload-flow UI remnants.

## Acceptance Criteria
1. Generated static reports do not render an upload section.
2. Generated static reports do not render an "Analyze Another" button.
3. Generated static reports contain no visible upload workflow text or controls.
4. Interactive web app behavior remains unchanged at `/`.
