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
python -m skicrowd predict 2027-01-16  # rank resorts for a date
python -m skicrowd calendar            # whole-season crowd calendar (HTML + CSV) in reports/
python -m skicrowd backtest            # 10-season simulation + insights report
```

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

## What 10 seasons of simulation say

From `reports/backtest_report.md`. These numbers are modelled from the
starting estimates, before any calibration.

* **Snowbasin is the default quiet pick.** It was least busy on 93% of plain
  weekdays, with a weekday crowd index of about 37 against 54–69 elsewhere.
* **On Saturdays, think Deer Valley.** It was least busy on about 54% of
  Saturdays and 42% of holidays. Ticket limits, skier-only rules and
  destination guests on changeover day keep it calmer than the
  local-heavy Cottonwoods.
* **Sundays beat Saturdays everywhere,** by roughly 5–20 index points.
* **Fridays and Mondays are the busiest weekdays.** Tuesday and Wednesday
  are the quietest.
* **Powder weekends:** Big Cottonwood (Brighton, Solitude) gets hit
  hardest, because it absorbs the overflow when Little Cottonwood's road is
  congested or closed. Deer Valley was the least-busy Ikon resort on about
  60% of powder weekends. Snowbasin, the usual quiet pick, was least busy
  on only 25%.
* **Brighton is the most crowded of the six** on about 78% of core-season
  days: close to town, cheap season pass, small footprint.
* **Early season is crowded everywhere.** November and December run
  roughly 30–70% higher than February because few runs are open. The
  quietest stretch is late March to mid-April on weekdays.
* **The worst days** are Christmas week, MLK and Presidents weekends, and
  powder Saturdays in thin-snow years.

## Training it on real data

The model starts from public-information estimates in
`skicrowd/config/model.toml`, and every number there is labelled as an
estimate. No Utah resort publishes daily skier counts, so the model gets
sharper as you feed it real evidence:

```bash
python -m skicrowd log 2027-01-09 brighton crowd_rating 8 --notes "Crest 15 min by 10am"
python -m skicrowd log 2027-01-09 LCC canyon_vehicles 6200
python -m skicrowd calibrate           # fits ~22 parameters, writes data/calibrated_params.toml
python -m skicrowd calibrate --synthetic-demo   # proves the fitter recovers a known truth
```

Supported evidence:

| Kind | What it is |
|---|---|
| `crowd_rating` | Your own 1–10 rating |
| `wait_min` | Lift wait in minutes |
| `parking_full_hour` | When reservations or lots sold out |
| `canyon_vehicles` | UDOT SR-210 / SR-190 counts |
| `visitors` | Actual skier counts |

Each subjective or proxy measure gets its own scale. The fitter only needs
the *ordering* to be informative.

**Highest-value next step:** get UDOT daily traffic counts for SR-210 and
SR-190 for past seasons. That is the closest public proxy for real
Cottonwood crowds, and it would turn "trained on prior seasons" from
simulated into measured.

In a controlled test (hidden "true" world, 400 noisy ratings, held-out days),
calibration raised least-busy accuracy from **72% to 93%** and rank
correlation from **0.67 to 0.95**.

## Project layout

```
skicrowd/config/model.toml   every assumption, documented and editable
skicrowd/calendar_features.py  holidays, school breaks, travel seasons
skicrowd/weather.py          SNOTEL history + Open-Meteo forecast -> daily features
skicrowd/model.py            demand -> choice -> congestion equilibrium
skicrowd/calibrate.py        MAP fitting to observations
skicrowd/analysis.py         backtests, climatology, point predictions
data/weather_daily.csv       cleaned SNOTEL history (4 stations, 2016-17 onward)
reports/                     generated backtest report and season calendar
tests/                       pytest suite
```

## Limitations

* Until it is calibrated, the absolute crowd index is an informed estimate.
  Trust the ranking more than the number.
* It's a daily model. Arriving at opening still beats any model, especially
  on LCC powder days.
* Open and close dates are typical dates gated by snow depth, and capacity
  figures are approximate. Deer Valley's 2025-26 expansion is modelled as a
  capacity step.
