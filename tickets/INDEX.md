# Skitur Tickets

## P0 - Critical
- [001](FIXED/001-elevation-profile-broken.md) ~~Elevation profile line not connected properly~~ FIXED
- [002](FIXED/002-heatmap-disconnects-on-zoom.md) ~~Heatmap disconnects from topo on browser zoom~~ FIXED
- [003](003-ground-colormap-not-sharp.md) Ground colormap still smoothed — REOPENED

## P1 - Important
- [010](010-map-fill-max-zoom.md) Map should fill all space at max zoom-out — REOPENED
- [011](FIXED/011-violin-plots-redesign.md) ~~Redesign violin/distribution plots~~ FIXED
- [012](WONTFIX/012-more-scoring-categories.md) ~~Think up more scoring categories~~ WONTFIX
- [013](FIXED/013-ground-colormap-orange-zone.md) ~~Ground colormap needs orange zone~~ FIXED
- [014](FIXED/014-track-cmap-to-20-degrees.md) ~~Track angle colormap extend to 20 degrees~~ FIXED
- [015](015-code-duplication-refactor.md) Refactor shared code between matplotlib and web versions
- [016](016-brainstorm-plots.md) Brainstorm and add useful plots
- [041](FIXED/041-remove-distribution-legend.md) ~~Remove Uphill/Downhill Legend~~ FIXED

## P2 - Nice to Have
- [020](020-chart-horizontal-space.md) Elevation/slope profile horizontal space efficiency
- [021](021-colormap-tick-alignment.md) Colormap ticks not touching bar, not aligned top/bottom
- [022](022-ground-legend-title.md) Ground colormap label/title positioning
- [023](FIXED/023-points-label.md) ~~"Points" -> "GPS points" in stats~~ FIXED
- [024](024-map-view-toggle.md) Add slope angle / topo+features toggle above map
- [025](025-fine-print-credits.md) Fine print section with credits, explanations, caveats
- [026](FIXED/026-distance-sigfigs.md) ~~Distance display - max 1 decimal place~~ FIXED
- [027](027-mit-license.md) Add MIT license
- [028](028-security-audit.md) Security audit
- [029](029-launchability-audit.md) Launchability audit
- [030](030-launch-plan.md) Launch plan / deployment / browser-side JS?
- [031](031-static-page-cli.md) Static page generation as proper CLI entrypoint
- [032](032-minimize-non-ascii.md) Minimize non-ASCII characters in source
- [033](033-track-segment-coloring.md) How does track segment coloring work? Should it gradient?
- [034](034-named-features.md) Named features (peaks, lakes, rivers, trails, roads)
- [036](FIXED/036-regen-debug-script.md) ~~Make regen_debug.sh script~~ FIXED
- [037](037-topo-line-labeling.md) Topo line labeling improvements
- [038](FIXED/038-visual-testing.md) ~~Set up visual testing / screenshots~~ FIXED
- [039](039-avy-time-stats.md) Add time-under-avy-slopes to stats
- [040](FIXED/040-skinnier-layout.md) ~~Make the whole data column skinnier~~ FIXED
- [044](FIXED/044-track-dist-hover-percent.md) ~~Track Distribution Hover Text and Y-axis Improvements~~ FIXED
- [047](FIXED/047-elevation-profile-thinner.md) ~~Make elevation profile a little thinner~~ FIXED

## P3 - Low Priority
- [042](FIXED/042-delete-avy-lines-ground-dist.md) ~~Delete Vertical Avy Lines~~ FIXED
- [043](043-tufte-audit.md) Tufte-style Visual Audit of Web Presentation
- [045](FIXED/045-ground-dist-degree-ticks.md) ~~Ground Angle Distribution - Degree Ticks on X-Axis~~ FIXED
- [046](FIXED/046-remove-plotly-watermark.md) ~~Remove "Produced with Plotly.js" Watermark~~ FIXED

## Questions for User (ANSWERED)
- Q1: (from #024) For the topo+features view, should we use OpenTopoMap tiles or something else? **-> OpenTopoMap is fine, unless we have a better idea.**
- Q2: (from #030) Any budget ceiling in mind for hosting? Target user count? **-> Low user count, budget immaterial.**
- Q3: (from #012) What aspects of a tour do YOU find most important beyond slope quality and avy? **-> Research what other people think.**
