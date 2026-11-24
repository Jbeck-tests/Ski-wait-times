# Real crowd data: UDOT traffic counts and webcams

## 1. UDOT historical traffic counts (training on past seasons)

`python -m skicrowd udot fetch` downloads hourly vehicle counts by direction
from UDOT's public ArcGIS service `CCS_Data` (table `CCS_Hourly`). No key is
needed. Coverage is currently **Jan 2022 to Mar 2025**, with gaps.

| Station | Location | Model target | Used in training |
|---|---|---|---|
| 317 | SR-210, mouth of Little Cottonwood | LCC (Alta + Snowbird) | yes |
| 322 | SR-190, mouth of Big Cottonwood | BCC (Brighton + Solitude) | yes |
| 822 | SR-190, end of the Brighton Loop (from 2024-25) | Brighton only | yes |
| 609 | SR-167, Trappers Loop | OGDEN (Snowbasin side) | yes (weight 0.6) |
| 320 | SR-39, Ogden Canyon | OGDEN (Ogden Valley) | yes (weight 0.4) |
| 824 | SR-224, Deer Valley Dr | Deer Valley | no: mostly town traffic, lower on weekends |
| 605 | SR-224 at Canyons Resort Dr | Park City Mountain | no: same reason |

### The signal

For each station and day, the signal is the number of vehicles heading up
toward the resorts between **05:00 and 13:59**. The up-canyon direction is
detected from the data (heavy mornings, light afternoons), not assumed.
Including the late morning means skiers who waited out an avalanche closure
still count. Days are dropped when:
- the sensor has gaps, or
- the count is under 35% of normal for that station, month and day type
  (an all-morning road closure or a sensor outage).

### How it trains

Each station is its own observation source with its own intercept and slope:
- The **intercept** absorbs non-ski traffic: employees, residents,
  backcountry skiers.
- The **slope** absorbs car occupancy.

Only the day-to-day pattern has to match.

### Held-out test

I trained on 2021-22 through 2023-24 and tested on 2024-25, which the fit
never saw.

| Station | Priors r | Trained r |
|---|---|---|
| LCC (317) | 0.80 | **0.87** |
| BCC (322) | 0.79 | 0.80 |
| Trappers Loop (609) | 0.75 | 0.76 |
| Ogden Canyon (320) | 0.61 | 0.62 |
| Brighton Loop (822), never in training | 0.64 | 0.65 |

### What the counts corrected

* **Powder pulls fewer extra people than assumed,** especially in Little
  Cottonwood, where traffic on 8"+ days is flat (×0.98). The road is the
  bottleneck: closures and the red snake cap how many cars get up.
* **Canyon roads saturate earlier.** The fitted morning capacity is about
  8,600 people for LCC and 7,500 for BCC, against priors of 13,000 and
  12,500.
* **School breaks matter much less** than assumed (×1.2, not ×1.9).
  Saturdays matter more.

### Getting more history

UDOT's main traffic-statistics site publishes hourly station data back to
1990 as Google Sheets. It sits behind a Cloudflare firewall that blocks cloud
IPs, so it can't be fetched from this environment. From a normal browser,
download the sheets for stations 317, 322, 609 and 320 and save them as CSV
in `data/udot/extra/`. An importer for that layout is a small addition. You
can also ask UDOT Traffic Statistics through the contact form linked from the
dataset page.

## 2. Live collector (GitHub Actions, `collector-data` branch)

`.github/workflows/collect.yml` runs every 20 minutes during ski hours
(7am–5pm Mountain), November through April.

**Self-scheduling.** GitHub's built-in cron never fired for this repo, so
each run starts the next one: collect, wait for the next 20-minute slot,
then dispatch the next run (`skicrowd/schedule.py`). Outside ski hours a
run just waits and hands off, up to the 6-hour job limit, so the chain
resumes at 7am. The chain stops after April. If it ever breaks, restart it
from **Actions → Collect crowd signals → Run workflow**. The cron is kept
as a backup and restarts the chain only if nothing else is running.

Each collecting run does four things, then rebuilds the dashboard.

1. **UDOT live canyon volume.** It logs UDOT's `CC_Volume` service, which
   reports net vehicles currently in Little and Big Cottonwood (up minus
   down). The daily peak between 8am and 2pm feeds calibration.
2. **Resort status.** It logs Ski Utah's open/closed list.
3. **Lift status.** It loads each resort's public lift-status page in a
   headless browser, reads which lifts are open (`skicrowd/lifts.py`), and
   logs it to `lift_status.csv`. Each lift-line camera is linked to its lift
   (`lift =` in `webcams.toml`), so counts taken while the lift is closed are
   ignored. Without this, a few skiers walking past a closed lift look like
   a short line. Snowbird's and Brighton's lists load from data servers this
   sandbox can't reach, so their parsers are confirmed from collector runs;
   a page that can't be parsed is saved to `debug/` for fixing. Check
   anytime with `python -m skicrowd lifts`.
4. **Webcams.** It reads the 26 cameras in `skicrowd/config/webcams.toml`
   (lift lines, base areas, parking lots and canyon-road cams) from Ski
   Utah's public webcam page:
   - Frames are analysed in memory and never saved.
   - A COCO-trained Faster R-CNN detector (torchvision, BSD-licensed)
     counts people and vehicles.
   - Each frame gets brightness and sharpness checks, so night, fog and
     snow on the lens are flagged unusable.
   - Frames that haven't refreshed since the last run are skipped.

5. **Dashboard.** `python -m skicrowd dashboard` turns the latest data into
   `site/data.json` and publishes the site to GitHub Pages (the `deploy`
   job). This needs **Settings → Pages → Source: GitHub Actions** once.

The resort status log builds a record of actual opening and closing dates
for future backtests.

Data is committed to the `collector-data` branch:
- `canyon_live.csv`
- `webcam_counts.csv` (now with `lift_open` per camera)
- `lift_status.csv`
- `resort_status.csv`
- `season_status.json`

### Using the collected data

```bash
git fetch origin collector-data
git worktree add collector-data origin/collector-data
python -m skicrowd collect summarize --data collector-data   # daily observations
python -m skicrowd calibrate --udot --collected collector-data
```

Each camera becomes its own observation source. Its daily value is the mean
count from 9:00 to 12:30, and the camera gets its own scale, so a wide shot
and a tight queue cam can both be used.

### Lifts, runs and the resort maps

* **Run status.** The collector keeps the latest text of each resort's
  lift/trail page in `pages/` (Brighton: its lift and trail rows, whose
  status, difficulty and grooming are icons). `skicrowd/runs.py` parses it
  when the dashboard builds. Snowbird's run lists are collapsed on its page,
  so the collector expands them first. Alta publishes terrain areas and
  gates by lift ("Ballroom via Collins") rather than named runs.
* **Maps.** The resorts' official trail maps are copyrighted artwork, so the
  map pages draw lifts and runs from OpenStreetMap (ODbL) on OpenTopoMap
  tiles, and link to the official report. To refresh the geometry, run
  **Actions → Fetch resort maps** (downloads to `collector-data/maps/`),
  then `python -m skicrowd.maps build --raw collector-data/maps` and commit
  `skicrowd/mapdata/`.
* **Runs per lift.** Brighton and Snowbasin group runs by lift on their own
  pages, and those groupings are used as given. Elsewhere it comes from the
  map's geometry: runs drawn downhill from near a lift's top that lead back
  to its bottom. OpenStreetMap names don't always match the resort's names,
  so a run can show on the map without a status.
* **Cameras on the map.** Lift-line cameras sit at the bottom of their lift.
  Base and parking cameras sit next to the lift they overlook
  (`near_lift`), or at `latlon` in `webcams.toml`. The live picture is
  loaded from Ski Utah only when someone taps "Show live camera".

### Splitting a canyon's cars between its resorts

UDOT's live feed gives one total per canyon. The dashboard splits it between
the canyon's open resorts (Brighton/Solitude, Alta/Snowbird):

1. **Model split.** Each resort's expected share of the canyon's skiers.
   Checked against UDOT's Brighton Loop counter (station 822, 2024-25): the
   model puts Brighton at ~55% of Big Cottonwood skiers, against ~49% of
   cars measured at the loop. The average is about right, but the measured
   split moves much more from day to day than the model's does.
2. **Webcam adjustment** (`dashboard.split_signals`). Each lift-line or
   parking camera reading becomes "count per 1,000 cars in the canyon". Its
   baseline is the median at the same hour on past *comparable* days, when
   the same resorts in that canyon were officially open and running lifts.
   Today's reading divided by the baseline says whether that resort is
   taking more or less of the canyon than usual, and the model's split is
   moved halfway toward it (`SPLIT_WEIGHT`). One resort's cameras are
   enough: if Solitude's lines are long for the traffic, Brighton gets less.
   It switches on by itself once a camera has `SPLIT_MIN_DAYS` (8)
   comparable days at that hour. Until then the dashboard shows "learning:
   N of 8 days".

**Stuck feeds.** Some feeds keep a fresh timestamp but serve the same
picture. In Nov 2026, Ski Utah's Brighton feeds showed a promotional graphic
and a summer frame. Readings are dropped when a picture appears on more
than one day, or when a camera's picture never changes all day, so stuck
feeds can't distort the counts. Set `split = false` on a camera in
`webcams.toml` to leave it out of the split (the misaimed Moonbeam lot cam is).

### Tuning once the season starts

* **Regions of interest.** Add an `roi` polygon to cameras whose frame
  includes slopes or roads that aren't part of the queue or lot. Do this
  after looking at a few busy-day frames.
* **Lift lines.** Dense lift lines make box detectors undercount. That's
  fine for ranking, because the count still rises with the queue, but a
  crowd-counting model would sharpen it.

### Limits and etiquette

* Ski Utah's cameras are public, but check its terms of use.
* Keep the cadence modest (20 minutes is gentle) and store only derived
  counts.
* GitHub disables scheduled workflows in public repos after 60 days with no
  repository activity. The collector's own data commits count as activity.
