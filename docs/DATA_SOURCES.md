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

Each collecting run does three things.

1. **UDOT live canyon volume.** It logs UDOT's `CC_Volume` service, which
   reports net vehicles currently in Little and Big Cottonwood (up minus
   down). The daily peak between 8am and 2pm feeds calibration.
2. **Resort status.** It logs Ski Utah's open/closed list.
3. **Webcams.** It reads the 26 cameras in `skicrowd/config/webcams.toml`
   (lift lines, base areas, parking lots and canyon-road cams) from Ski
   Utah's public webcam page:
   - Frames are analysed in memory and never saved.
   - A COCO-trained Faster R-CNN detector (torchvision, BSD-licensed)
     counts people and vehicles.
   - Each frame gets brightness and sharpness checks, so night, fog and
     snow on the lens are flagged unusable.
   - Frames that haven't refreshed since the last run are skipped.

The resort status log builds a record of actual opening and closing dates
for future backtests.

Data is committed to the `collector-data` branch:
- `canyon_live.csv`
- `webcam_counts.csv`
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
