# skicrowd: which Utah Ikon resort will be least crowded?

A crowd-prediction model for the six Utah Ikon Pass resorts: **Alta, Snowbird,
Brighton, Solitude, Deer Valley and Snowbasin**. Park City Mountain and Powder
Mountain are modelled as competitors because they pull skiers away.

It answers two questions:

* **"Where should I ski on *date*?"**: a ranked list with an expected crowd
  index and the probability that each resort is the least busy.
* **"How do I avoid crowds in general?"**: rules of thumb learned by
  simulating every day of the last 10 seasons against the snow that actually
  fell.

## Quick start

```bash
pip install -e .                       # numpy, pandas, scipy
python -m skicrowd fetch               # SNOTEL snow history (already cached in data/)
python -m skicrowd status              # which resorts are open / announced opening dates (Ski Utah)
python -m skicrowd predict 2027-01-16  # rank resorts for a date
python -m skicrowd calendar            # whole-season crowd calendar (HTML + CSV) in reports/
python -m skicrowd backtest            # 10-season simulation + insights report
```

**Open/closed days** for the current season come from Ski Utah's snow report:
- A resort reported open is open.
- "Opening Nov 27" means closed until then.
- "TBA" means closed for now.

`predict` and `calendar` refresh this automatically, and the GitHub
collector logs it daily. Past seasons, and dates that are still TBA, fall
back to typical opening dates gated on snow depth.

`predict` picks the best data source available for the date:
- **Within 16 days:** the Open-Meteo forecast (snow, temperature, wind).
- **If the forecast is unavailable:** this season's measured snowpack,
  assuming no new snow.
- **Further out:** every past winter as a scenario (climatology).

## How it works (short version)

The full write-up is in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

1. **Who skis today?** Five skier groups:
   - Salt Lake County locals
   - Ogden-area locals
   - Park City locals
   - destination visitors lodged in Park City
   - destination visitors lodged in Salt Lake City and the Cottonwoods

   Each group's size depends on:
   - the calendar: weekends, school breaks, destination travel weeks, and
     Utah-specific quirks such as lighter Sundays and LDS General Conference
     weekend
   - powder
   - cold, rain and wind
   - base depth
   - whether their pass resort is even open
2. **Where do they go?** A discrete-choice (logit) model weighs:
   - pass and brand affinity
   - fresh snow at each resort
   - drive time
   - canyon road delays (red-snake congestion plus avalanche-control
     closures)
   - **how crowded each resort will be**
3. **Equilibrium.** Crowds and road delays feed back into everyone's choices,
   so each day is solved iteratively. Parking-reservation and ticket caps
   push overflow to other resorts.
4. **Real history.** Snow and temperature come from USDA SNOTEL stations next
   to each resort, hourly, 2016-17 to today. They are cleaned of sensor
   glitches and read as of 6am, when you decide where to go.
5. **Training.** `calibrate` fits the model's parameters to real crowd
   observations by MAP estimation (see below).

## What the model says (trained on real UDOT traffic counts)

From `reports/backtest_report.md`: 10 seasons of real snow, run through the
model after training on 2022–2025 canyon traffic counts.

* **Weekdays: Snowbasin.** It was least busy on 81% of plain weekdays, with
  a weekday crowd index of about 50 against 60–76 elsewhere. Tuesday and
  Wednesday are the quietest days everywhere; Fridays are noticeably busier.
* **Saturdays: Deer Valley or Snowbird, not Snowbasin.** Trappers Loop
  counts show a big Ogden-area weekend surge, so Snowbasin's Saturday index
  (about 121) is among the worst. Deer Valley and Snowbird were each least
  busy on about 36% of Saturdays. On holiday weekends it's Deer Valley at
  49%.
* **Powder days: Snowbird,** least busy on 46% of powder weekdays and 49% of
  powder weekends. The catch: the Little Cottonwood road is the bottleneck
  (traffic counts are *flat* on 8"+ days). The mountain stays relatively
  calm because the road meters people in, so you pay in canyon delay.
  `predict` shows the expected delay.
* **Brighton is the most crowded** of the six on about two-thirds of days.
* **School breaks matter much less than assumed.** Utah's Christmas and
  spring breaks barely move canyon traffic beyond the holidays themselves.
* **Early season** runs 30–50% busier than February, because there are few
  runs open. The quietest stretch is weekdays from late March to mid-April.

## Real data

* **UDOT traffic counts (history).** `python -m skicrowd udot fetch` pulls
  hourly vehicle counts from UDOT's public count stations at the mouths of
  Little and Big Cottonwood, the Brighton Loop, Trappers Loop and Ogden
  Canyon (Jan 2022–Mar 2025). Then `python -m skicrowd calibrate --udot`
  trains on them. On the held-out 2024-25 season, training raised the
  correlation with actual Little Cottonwood traffic from 0.80 to 0.86. It
  also improved Brighton-only traffic from 0.64 to 0.68, though the model
  was never trained on that counter.
* **Live collector (GitHub Actions).** `.github/workflows/collect.yml` runs
  every 20 minutes during ski hours. It logs UDOT's live "vehicles currently
  in the canyon" for Little and Big Cottonwood, and counts people and cars
  in 26 resort webcams (lift lines, base areas, lots, canyon roads). Results
  go to the `collector-data` branch. Train on them with
  `python -m skicrowd calibrate --udot --collected collector-data`.
* **Your own days:**
  `python -m skicrowd log 2027-01-09 brighton crowd_rating 8`

Details and caveats are in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Project layout

```
skicrowd/config/model.toml   every assumption, documented and editable
skicrowd/calendar_features.py  holidays, school breaks, travel seasons
skicrowd/weather.py          SNOTEL history + Open-Meteo forecast -> daily features
skicrowd/model.py            demand -> choice -> congestion equilibrium
skicrowd/calibrate.py        MAP fitting to observations
skicrowd/analysis.py         backtests, climatology, point predictions
skicrowd/status.py           live open/closed status + announced opening dates
skicrowd/udot.py             UDOT traffic-count importer + live canyon volume
skicrowd/collect.py          webcam + live canyon collector (GitHub Actions)
skicrowd/config/webcams.toml cameras the collector reads
data/weather_daily.csv       cleaned SNOTEL history (4 stations, 2016-17 onward)
data/udot/                   UDOT hourly counts + daily ski signal
data/calibrated_params.toml  parameters trained on UDOT counts (delete to revert to priors)
reports/                     generated backtest report and season calendar
tests/                       pytest suite
```

## Limitations

* Road counts see canyons, not individual resorts. Splits within a canyon
  (Alta vs Snowbird, Brighton vs Solitude) and Deer Valley still rest
  mostly on priors until webcam data accumulates. Trust the ranking more
  than the absolute number.
* It's a daily model. Arriving at opening still beats any model, especially
  on LCC powder days.
* Open and close dates for past seasons are typical dates gated by snow
  depth (the collector is now recording real ones), and capacity figures
  are approximate. Deer Valley's 2025-26 expansion is modelled as a
  capacity step.
