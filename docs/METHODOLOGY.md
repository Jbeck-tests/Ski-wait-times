# How skicrowd works

## The problem with "just train a model"

No Utah resort publishes daily skier counts or historical lift-line data. A
model "trained on prior seasons" with no ground truth would only memorise
whatever assumptions produced its labels. So skicrowd is built in two layers:

1. **A structural model.** It encodes *why* crowds happen: calendar, snow,
   roads, capacity, pass lock-in and people avoiding crowds. It has
   interpretable parameters set from public information, and it is driven by
   **real measured history** (SNOTEL snow and temperature, 2016-17 onward).
2. **A calibration layer.** It re-fits those parameters to whatever real
   crowd evidence you collect. It works with as few as about 10 data points,
   and gets steadily better as you add more.

This structure is the moat. Anyone can say "go to Snowbasin on a Tuesday".
What this adds is the interactions:
- A powder Saturday when Little Cottonwood is closed until 10am.
- A cold snap in Christmas week.
- A thin-base December, when all of Brighton is on three runs.

## Layer 1: daily demand-and-choice equilibrium

### Who skis (`segments` in `config/model.toml`)

| Segment | Driven by |
|---|---|
| SLC / Utah County locals | weekends, school breaks, powder, cold, base depth |
| Ogden / Davis / Weber locals | same, closer to Snowbasin |
| Park City locals | same, closer to Deer Valley / Park City |
| Destination visitors lodged in Park City | travel seasons (Christmas, Presidents week, March breaks), Saturday changeover |
| Destination visitors lodged in SLC / the Cottonwoods | same, Ikon-heavy |

Intending skiers per segment per day:

```
N = base x calendar_multiplier x month_factor x powder_factor x weather_factor x availability^lockin
```

* **calendar_multiplier**: weekday shape (Fridays > midweek), Saturday ≫
  Sunday (a strong Utah effect), holiday Mondays, school breaks, Christmas Day,
  Easter Sunday, and LDS General Conference weekend. Destination segments use
  a travel-season curve instead.
* **powder_factor**: about +5.5% locals per inch of new snow, capped.
* **weather_factor**: ski-hours temperature below 10°F (and much more below
  0°F), rain, high wind (forecast only), thin base.
* **availability^lockin**: most people ski where their pass is. Before
  Alta/Solitude open, or after Brighton closes, those pass holders mostly
  don't ski rather than buying tickets elsewhere.

### Where they go (multinomial logit)

For segment *s* choosing resort *r* (or staying home):

```
U[s,r] = affinity[s,r]                        pass ownership, loyalty, skier-only, brand
       + beta_powder[s] * powder[r]           fresh snow AT THAT RESORT (SNOTEL station)
       + powder_bonus[r] * min(powder/12, 1)  reputation pull on deep days (Alta, Snowbird)
       - beta_travel[s] * (drive + road_delay[canyon(r)]) / 60
       - beta_crowd * load[r]                 people avoid the zoo
       - shadow_price[r]                      parking reservation / ticket caps
```

### Feedback loops, solved to equilibrium each day

* **Crowding**: `load = visitors / effective_capacity`. Effective capacity
  shrinks with a thin base (less terrain open) and high wind.
* **Canyon roads**: LCC (Alta, Snowbird) and BCC (Brighton, Solitude) each
  carry their resorts' combined traffic. Delay follows a BPR congestion curve
  (the "red snake"). On top of that is a weather-driven avalanche-closure
  delay, a logistic function of 24-hour snowfall.
* **Caps**: Alta parking reservations and Deer Valley ticket limits are
  enforced with a shadow price that rises until demand fits, pushing the
  excess elsewhere.

The fixed point is found by the method of successive averages. This is
standard for congestion games, and a test checks convergence.

Output: **crowd index** = 100 × load. 100 means the resort is at
comfortable capacity.

### Snow inputs

Hourly SNOTEL (USDA NRCS) at:

| Station | Used for |
|---|---|
| Snowbird #766 | Alta, Snowbird, and the LCC road |
| Brighton #366 | Brighton, Solitude, and the BCC road |
| Thaynes Canyon #814 | Deer Valley, Park City |
| Ben Lomond Peak #332 | Snowbasin, Powder Mountain |

New snow is measured as of 6am, which is what you see when you decide where
to go. It comes from the precipitation gauge times a temperature-dependent
snow:liquid ratio, cross-checked against the rise in snow depth.

The data has some quirks:
- Sensor noise below about 1" of water-equivalent or 2.5" of depth is
  ignored.
- Physically impossible depths (stuck sensors; Snowbird spent weeks of
  2023-24 reading 285") are replaced with a value estimated from SWE.
- Station snowfall is scaled by 1.25 to approximate resort snow reports,
  because skiers react to the report, not the gauge.

Forecasts (next 16 days) come from Open-Meteo at each resort's coordinates
and elevation, reduced to the same features.

### Planning further ahead (climatology)

For dates beyond the forecast, `calendar` and `predict` run the target date's
**calendar** against **each of the past 10 winters' weather**. That gives:
- an expected crowd index,
- a 10th–90th percentile range,
- the probability that each resort is the least busy.

## Layer 2: training on real observations

`python -m skicrowd calibrate` fits about 22 parameters:
- segment sizes,
- the Saturday and Sunday effects,
- powder sensitivity,
- crowd aversion,
- each Ikon resort's capacity scale and local affinity.

It uses maximum a-posteriori estimation:

* **Likelihood**: Gaussian with unknown variance per observation type. For
  subjective or proxy measures (crowd ratings, lift waits, the hour parking
  sold out, canyon vehicle counts), each type gets its own intercept and
  slope, solved in closed form inside the loop. So scales and units don't
  matter; only ordering does. Direct visitor counts are compared on a log
  scale.
* **Prior**: Gaussian around the config values. A handful of observations
  nudges the model; hundreds can overrule it.
* **Optimiser**: L-BFGS-B over transformed parameters (log for positive
  quantities).

### Proof the training works

`python -m skicrowd calibrate --synthetic-demo` runs a controlled test:
1. It builds a hidden "true" world that differs from the priors (Solitude
   much more popular, Snowbasin 30% less roomy, worse Saturdays, stronger
   powder response).
2. It simulates 400 noisy 1–10 crowd ratings on half the days.
3. It fits, then scores on the *other* half of the days.

On the current model, the fit raised least-busy-resort accuracy from 72% to
93%, and mean rank correlation from 0.67 to 0.95. It recovered the planted
parameters too: Snowbasin capacity 1.0 → 0.74 (truth 0.70), Saturday
multiplier 2.8 → 3.69 (3.8), and powder response 0.055 → 0.080 (0.09). The
remaining gap, such as Solitude's affinity reaching 0.75 against a truth of
1.2, is the prior doing its job with limited data.

### Best sources of real labels (in order of value)

1. **UDOT continuous traffic counts** on SR-210 (LCC) and SR-190 (BCC).
   These are daily vehicle counts that are public on request or through
   UDOT's data portals. Years of history are available, so this is the
   single best way to train on prior seasons. Log them as `canyon_vehicles`.
2. **Parking-reservation sell-out times** (Alta, Snowbird, Solitude,
   Brighton) and resort lot-full announcements. Log them as
   `parking_full_hour`.
3. **Lift-wait readings** wherever a resort app or site shows them, captured
   on a schedule. Log them as `wait_min`.
4. **Your own days**: a 1–10 rating per resort-day you ski, as
   `crowd_rating`. Twenty or thirty of these already sharpen the
   resort-specific parameters.
5. **Parking-lot webcams**: counting cars on public resort cams at 10am is
   a strong, cheap, repeatable crowd signal.

## Known limitations

* Before calibration, absolute crowd numbers are informed estimates. Rankings
  are more robust than levels.
* There is no time-of-day dimension. Being at the lift at opening beats
  every model, especially on LCC powder days.
* Capacity figures are approximate. Deer Valley's East Village expansion
  (2025-26 onward) is modelled as a capacity step-up and should be verified.
* Resort open and close dates use typical dates gated on snow depth, not the
  actual published dates.
* The Ikon Base pass, blackout dates, and resort promotions (such as
  discounted-ticket days) aren't modelled yet.
* Wind holds exist only in forecast mode; SNOTEL has no wind history.
