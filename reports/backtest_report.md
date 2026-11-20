# Utah Ikon crowd backtest: 2016-17 to 2025-26

_Generated 2026-11-20. Snow and temperature are **measured** SNOTEL history; the crowd numbers are **modelled** from those conditions plus the calendar, using the parameters in `skicrowd/config/model.toml` (or `data/calibrated_params.toml` if you've calibrated)._

**Crowd index**: 100 = the resort is at its comfortable capacity. Below 50 is quiet, 50-80 moderate, 80-110 busy, 110+ packed. 'Core season' means days when all six Ikon resorts were open.

## Overall
| Resort | avg crowd (core season) | % days least busy | % days busiest |
|---|---|---|---|
| Snowbasin | 55 | 79 | 4 |
| Deer Valley | 67 | 16 | 7 |
| Snowbird | 69 | 4 | 0 |
| Solitude | 73 | 1 | 0 |
| Alta | 75 | 0 | 11 |
| Brighton | 87 | 0 | 78 |

## Average crowd index by day type
| Resort | weekday | saturday | sunday | holiday-week weekday | holiday |
|---|---|---|---|---|---|
| Alta | 60 | 96 | 85 | 88 | 116 |
| Brighton | 69 | 116 | 104 | 100 | 125 |
| Deer Valley | 55 | 78 | 73 | 83 | 98 |
| Snowbasin | 37 | 84 | 65 | 66 | 100 |
| Snowbird | 54 | 89 | 79 | 81 | 105 |
| Solitude | 57 | 97 | 85 | 86 | 114 |

## How often each resort was the least busy (% of days of that type)
| Resort | weekday | saturday | sunday | holiday-week weekday | holiday |
|---|---|---|---|---|---|
| Deer Valley | 3 | 54 | 23 | 10 | 42 |
| Snowbasin | 93 | 40 | 73 | 83 | 50 |
| Snowbird | 3 | 5 | 4 | 5 | 8 |
| Solitude | 2 | 1 | 0 | 2 | 0 |

## Powder effects (LCC new snow >= 8" = powder day)
| Resort | weekday / day after storm | weekday / no new snow | weekday / powder day | weekend/holiday / day after storm | weekend/holiday / no new snow | weekend/holiday / powder day |
|---|---|---|---|---|---|---|
| Alta | 73 | 58 | 72 | 108 | 98 | 113 |
| Brighton | 74 | 67 | 88 | 123 | 114 | 127 |
| Deer Valley | 56 | 55 | 66 | 89 | 83 | 98 |
| Snowbasin | 37 | 36 | 47 | 85 | 80 | 111 |
| Snowbird | 67 | 52 | 65 | 101 | 89 | 106 |
| Solitude | 59 | 55 | 71 | 105 | 97 | 114 |

### Least-busy share on snow days (%)
| Resort | weekday / day after storm | weekday / no new snow | weekday / powder day | weekend/holiday / day after storm | weekend/holiday / no new snow | weekend/holiday / powder day |
|---|---|---|---|---|---|---|
| Deer Valley | 2 | 2 | 5 | 32 | 35 | 60 |
| Snowbasin | 95 | 94 | 81 | 61 | 61 | 25 |
| Snowbird | 2 | 2 | 10 | 7 | 4 | 14 |
| Solitude | 0 | 2 | 4 | 0 | 0 | 2 |

## By month
| Resort | Nov | Dec | Jan | Feb | Mar | Apr |
|---|---|---|---|---|---|---|
| Alta | 108 | 107 | 80 | 75 | 68 | 43 |
| Brighton | 116 | 117 | 92 | 88 | 83 | 54 |
| Deer Valley |  | 85 | 69 | 70 | 66 | 38 |
| Snowbasin | 93 | 86 | 58 | 54 | 48 | 27 |
| Snowbird | 105 | 95 | 72 | 70 | 64 | 38 |
| Solitude | 101 | 100 | 77 | 73 | 69 | 44 |

## By day of week (non-holiday core season)
| Resort | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|---|
| Alta | 61 | 57 | 57 | 60 | 66 | 96 | 85 |
| Brighton | 71 | 65 | 65 | 70 | 77 | 116 | 104 |
| Deer Valley | 56 | 54 | 53 | 55 | 59 | 78 | 73 |
| Snowbasin | 38 | 34 | 34 | 37 | 43 | 84 | 65 |
| Snowbird | 55 | 52 | 51 | 54 | 60 | 89 | 79 |
| Solitude | 58 | 53 | 54 | 57 | 63 | 97 | 85 |

## By season (snowy winters vs. lean ones)
| Resort | 2016-17 | 2017-18 | 2018-19 | 2019-20 | 2020-21 | 2021-22 | 2022-23 | 2023-24 | 2024-25 | 2025-26 |
|---|---|---|---|---|---|---|---|---|---|---|
| Alta | 73 | 80 | 74 | 74 | 83 | 74 | 68 | 77 | 74 | 75 |
| Brighton | 83 | 96 | 84 | 85 | 96 | 93 | 82 | 87 | 90 | 98 |
| Deer Valley | 62 | 74 | 63 | 66 | 73 | 69 | 61 | 65 | 70 | 68 |
| Snowbasin | 53 | 68 | 54 | 56 | 60 | 63 | 53 | 54 | 60 | 69 |
| Snowbird | 62 | 69 | 59 | 60 | 66 | 63 | 55 | 61 | 66 | 67 |
| Solitude | 70 | 85 | 70 | 72 | 79 | 81 | 68 | 76 | 78 | 82 |

## Worst system-wide days, last three seasons
| date | mean_crowd | day_type | snow | least busy | its crowd | busiest | busiest crowd |
|---|---|---|---|---|---|---|---|
| Sat 2026-01-03 | 142 | saturday | no new snow | Deer Valley | 101 | Alta | 168 |
| Fri 2026-01-02 | 134 | holiday-week weekday | powder day | Deer Valley | 102 | Alta | 151 |
| Sat 2023-12-30 | 132 | holiday | no new snow | Snowbird | 114 | Alta | 156 |
| Sat 2023-12-23 | 132 | holiday | no new snow | Snowbird | 114 | Alta | 160 |
| Sun 2023-12-31 | 130 | holiday | no new snow | Snowbird | 113 | Alta | 156 |
| Sat 2025-02-15 | 128 | holiday | powder day | Deer Valley | 106 | Snowbasin | 145 |
| Sun 2023-12-24 | 128 | holiday | no new snow | Deer Valley | 112 | Alta | 146 |
| Sat 2024-12-28 | 127 | holiday | powder day | Deer Valley | 102 | Alta | 145 |
| Sat 2024-01-06 | 127 | saturday | no new snow | Deer Valley | 109 | Brighton | 141 |
| Thu 2023-12-28 | 126 | holiday | no new snow | Snowbasin | 100 | Alta | 150 |
| Fri 2023-12-29 | 126 | holiday | no new snow | Snowbasin | 99 | Alta | 150 |
| Wed 2023-12-27 | 126 | holiday | no new snow | Snowbasin | 101 | Alta | 146 |
| Sun 2024-12-29 | 124 | holiday | powder day | Deer Valley | 100 | Alta | 141 |
| Sat 2025-02-08 | 124 | saturday | powder day | Deer Valley | 104 | Snowbasin | 138 |
| Sat 2024-01-13 | 124 | holiday | no new snow | Deer Valley | 102 | Snowbasin | 142 |
| Mon 2024-01-01 | 124 | holiday | no new snow | Snowbasin | 91 | Alta | 149 |
| Tue 2023-12-26 | 124 | holiday | no new snow | Snowbasin | 96 | Alta | 145 |
| Sat 2023-12-09 | 122 | saturday | no new snow | Deer Valley | 104 | Snowbasin | 133 |
| Mon 2024-12-30 | 121 | holiday | powder day | Deer Valley | 100 | Snowbasin | 136 |
| Mon 2024-01-15 | 120 | holiday | powder day | Deer Valley | 103 | Snowbasin | 133 |

## Quietest core-season days, last three seasons
| date | mean_crowd | day_type | snow | least busy | its crowd | busiest | busiest crowd |
|---|---|---|---|---|---|---|---|
| Wed 2023-04-12 | 18 | weekday | no new snow | Snowbasin | 12 | Brighton | 22 |
| Tue 2023-04-11 | 19 | weekday | no new snow | Snowbasin | 13 | Brighton | 22 |
| Wed 2024-04-10 | 23 | weekday | no new snow | Snowbasin | 14 | Brighton | 32 |
| Mon 2023-04-10 | 23 | weekday | no new snow | Snowbasin | 16 | Brighton | 29 |
| Tue 2025-04-08 | 24 | weekday | no new snow | Snowbasin | 14 | Brighton | 31 |
| Wed 2025-04-09 | 24 | weekday | no new snow | Snowbasin | 15 | Brighton | 31 |
| Tue 2024-04-09 | 25 | weekday | no new snow | Snowbasin | 17 | Brighton | 31 |
| Mon 2025-04-07 | 25 | weekday | no new snow | Snowbasin | 16 | Brighton | 33 |
| Thu 2025-04-10 | 25 | weekday | no new snow | Snowbasin | 16 | Brighton | 33 |
| Fri 2023-04-07 | 25 | weekday | no new snow | Snowbasin | 18 | Brighton | 32 |

