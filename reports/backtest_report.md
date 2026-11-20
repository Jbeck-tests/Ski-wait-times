# Utah Ikon crowd backtest: 2016-17 to 2025-26

_Generated 2026-11-20. Snow and temperature are **measured** SNOTEL history; the crowd numbers are **modelled** from those conditions plus the calendar, using the parameters in `skicrowd/config/model.toml` (or `data/calibrated_params.toml` if you've calibrated)._

**Crowd index**: 100 = the resort is at its comfortable capacity. Below 50 is quiet, 50-80 moderate, 80-110 busy, 110+ packed. 'Core season' means days when all six Ikon resorts were open.

## Overall
| Resort | avg crowd (core season) | % days least busy | % days busiest |
|---|---|---|---|
| Snowbasin | 73 | 56 | 16 |
| Snowbird | 74 | 19 | 0 |
| Deer Valley | 76 | 18 | 8 |
| Solitude | 79 | 8 | 0 |
| Alta | 80 | 0 | 10 |
| Brighton | 91 | 0 | 66 |

## Average crowd index by day type
| Resort | weekday | saturday | sunday | holiday-week weekday | holiday |
|---|---|---|---|---|---|
| Alta | 66 | 103 | 92 | 88 | 114 |
| Brighton | 76 | 117 | 108 | 98 | 121 |
| Deer Valley | 62 | 97 | 89 | 92 | 102 |
| Snowbasin | 50 | 121 | 90 | 84 | 120 |
| Snowbird | 60 | 95 | 85 | 81 | 103 |
| Solitude | 65 | 101 | 90 | 87 | 112 |

## How often each resort was the least busy (% of days of that type)
| Resort | weekday | saturday | sunday | holiday-week weekday | holiday |
|---|---|---|---|---|---|
| Deer Valley | 7 | 36 | 27 | 7 | 49 |
| Snowbasin | 81 | 4 | 27 | 51 | 9 |
| Snowbird | 10 | 36 | 26 | 29 | 33 |
| Solitude | 2 | 24 | 19 | 13 | 8 |

## Powder effects (LCC new snow >= 8" = powder day)
| Resort | weekday / day after storm | weekday / no new snow | weekday / powder day | weekend/holiday / day after storm | weekend/holiday / no new snow | weekend/holiday / powder day |
|---|---|---|---|---|---|---|
| Alta | 76 | 66 | 67 | 106 | 103 | 105 |
| Brighton | 79 | 74 | 91 | 118 | 114 | 119 |
| Deer Valley | 63 | 61 | 76 | 102 | 95 | 102 |
| Snowbasin | 51 | 49 | 65 | 119 | 106 | 130 |
| Snowbird | 70 | 60 | 61 | 99 | 94 | 97 |
| Solitude | 65 | 64 | 75 | 102 | 100 | 107 |

### Least-busy share on snow days (%)
| Resort | weekday / day after storm | weekday / no new snow | weekday / powder day | weekend/holiday / day after storm | weekend/holiday / no new snow | weekend/holiday / powder day |
|---|---|---|---|---|---|---|
| Deer Valley | 16 | 6 | 7 | 21 | 36 | 40 |
| Snowbasin | 74 | 86 | 43 | 11 | 18 | 2 |
| Snowbird | 5 | 6 | 46 | 43 | 29 | 49 |
| Solitude | 5 | 2 | 4 | 25 | 18 | 9 |

## By month
| Resort | Nov | Dec | Jan | Feb | Mar | Apr |
|---|---|---|---|---|---|---|
| Alta | 106 | 109 | 86 | 80 | 73 | 50 |
| Brighton | 119 | 120 | 97 | 91 | 85 | 64 |
| Deer Valley |  | 90 | 77 | 79 | 77 | 44 |
| Snowbasin | 112 | 101 | 77 | 75 | 68 | 38 |
| Snowbird | 112 | 100 | 78 | 74 | 69 | 46 |
| Solitude | 105 | 106 | 84 | 78 | 73 | 53 |

## By day of week (non-holiday core season)
| Resort | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|---|
| Alta | 67 | 64 | 63 | 66 | 71 | 103 | 92 |
| Brighton | 77 | 72 | 72 | 76 | 83 | 117 | 108 |
| Deer Valley | 63 | 60 | 59 | 62 | 67 | 97 | 89 |
| Snowbasin | 52 | 46 | 46 | 50 | 58 | 121 | 90 |
| Snowbird | 61 | 58 | 58 | 61 | 65 | 95 | 85 |
| Solitude | 66 | 62 | 62 | 65 | 69 | 101 | 90 |

## By season (snowy winters vs. lean ones)
| Resort | 2016-17 | 2017-18 | 2018-19 | 2019-20 | 2020-21 | 2021-22 | 2022-23 | 2023-24 | 2024-25 | 2025-26 |
|---|---|---|---|---|---|---|---|---|---|---|
| Alta | 76 | 85 | 79 | 78 | 89 | 79 | 72 | 81 | 79 | 82 |
| Brighton | 85 | 102 | 86 | 90 | 103 | 100 | 83 | 90 | 95 | 105 |
| Deer Valley | 71 | 81 | 72 | 74 | 80 | 77 | 70 | 73 | 78 | 77 |
| Snowbasin | 71 | 86 | 71 | 74 | 78 | 81 | 71 | 71 | 78 | 90 |
| Snowbird | 66 | 76 | 64 | 64 | 73 | 69 | 60 | 67 | 72 | 75 |
| Solitude | 73 | 93 | 74 | 77 | 87 | 90 | 70 | 80 | 84 | 91 |

## Worst system-wide days, last three seasons
| date | mean_crowd | day_type | snow | least busy | its crowd | busiest | busiest crowd |
|---|---|---|---|---|---|---|---|
| Sat 2026-01-03 | 142 | saturday | no new snow | Deer Valley | 102 | Alta | 166 |
| Sat 2023-12-23 | 135 | holiday | no new snow | Deer Valley | 115 | Alta | 164 |
| Sun 2023-12-31 | 134 | holiday | no new snow | Deer Valley | 115 | Alta | 159 |
| Sat 2023-12-30 | 134 | holiday | no new snow | Deer Valley | 115 | Alta | 158 |
| Sun 2023-12-24 | 132 | holiday | no new snow | Deer Valley | 112 | Alta | 149 |
| Sat 2023-12-16 | 132 | saturday | no new snow | Snowbird | 114 | Alta | 150 |
| Sat 2024-01-06 | 128 | saturday | no new snow | Deer Valley | 110 | Solitude | 141 |
| Thu 2023-12-28 | 128 | holiday | no new snow | Snowbird | 114 | Alta | 150 |
| Fri 2023-12-29 | 127 | holiday | no new snow | Snowbird | 113 | Alta | 149 |
| Wed 2023-12-27 | 127 | holiday | no new snow | Snowbird | 114 | Alta | 146 |
| Mon 2024-01-01 | 126 | holiday | no new snow | Snowbasin | 111 | Alta | 151 |
| Sun 2024-01-07 | 125 | sunday | no new snow | Deer Valley | 107 | Brighton | 138 |
| Tue 2023-12-26 | 125 | holiday | no new snow | Snowbird | 114 | Alta | 143 |
| Sun 2023-12-10 | 123 | sunday | no new snow | Deer Valley | 107 | Brighton | 132 |
| Sat 2026-03-28 | 123 | saturday | no new snow | Deer Valley | 95 | Brighton | 152 |
| Mon 2024-12-30 | 122 | holiday | powder day | Deer Valley | 100 | Snowbasin | 136 |
| Fri 2026-01-02 | 122 | holiday-week weekday | powder day | Snowbird | 101 | Brighton | 141 |
| Sun 2024-12-29 | 121 | holiday | powder day | Deer Valley | 100 | Alta | 136 |
| Sat 2024-12-28 | 121 | holiday | powder day | Deer Valley | 101 | Alta | 146 |
| Sat 2024-01-13 | 120 | holiday | no new snow | Deer Valley | 102 | Snowbasin | 143 |

## Quietest core-season days, last three seasons
| date | mean_crowd | day_type | snow | least busy | its crowd | busiest | busiest crowd |
|---|---|---|---|---|---|---|---|
| Wed 2023-04-12 | 22 | weekday | no new snow | Snowbasin | 16 | Brighton | 27 |
| Tue 2023-04-11 | 23 | weekday | no new snow | Snowbasin | 18 | Brighton | 27 |
| Wed 2024-04-10 | 27 | weekday | no new snow | Snowbasin | 20 | Brighton | 36 |
| Mon 2023-04-10 | 29 | weekday | no new snow | Snowbasin | 21 | Brighton | 35 |
| Tue 2025-04-08 | 29 | weekday | no new snow | Snowbasin | 20 | Brighton | 37 |
| Wed 2025-04-09 | 29 | weekday | no new snow | Snowbasin | 20 | Brighton | 38 |
| Tue 2024-04-09 | 30 | weekday | no new snow | Snowbasin | 22 | Brighton | 37 |
| Mon 2025-04-07 | 31 | weekday | no new snow | Snowbasin | 22 | Brighton | 40 |
| Thu 2024-04-11 | 31 | weekday | no new snow | Snowbasin | 21 | Brighton | 42 |
| Thu 2025-04-10 | 31 | weekday | no new snow | Snowbasin | 21 | Brighton | 40 |

