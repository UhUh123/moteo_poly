# Temperature Model Report

- model: `sklearn_hist_gradient_boosting_bias_corrector`
- training rows: `124440`
- holdout rows: `41106`
- train period: `2023-01-01` to `2025-03-27`
- holdout period: `2025-03-28` to `2026-05-04`
- features: `target_day_of_year_sin, target_day_of_year_cos, target_month, target_is_weekend, target_is_max, station_latitude, station_longitude, station_elevation_m, station_id_hash, source_domain_hash, forecast_temp_max_c, forecast_temp_min_c, forecast_temp_mean_c, forecast_temp_spread_c`

| split | group | model | samples | MAE C | RMSE C | bias C | within 1C | within 2C | within 3C |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| holdout | combined | nwp_daily_extreme_baseline | 41106 | 0.5611 | 1.0885 | 0.1284 | 80.21% | 93.25% | 97.47% |
| holdout | max | nwp_daily_extreme_baseline | 20553 | 0.5981 | 1.1908 | 0.0829 | 79.4% | 92.19% | 96.87% |
| holdout | min | nwp_daily_extreme_baseline | 20553 | 0.5241 | 0.9756 | 0.1739 | 81.01% | 94.31% | 98.07% |
| holdout | combined | sklearn_hist_gradient_boosting_bias_corrector | 41106 | 0.4812 | 0.8579 | -0.0875 | 81.93% | 95.59% | 98.89% |
| holdout | max | sklearn_hist_gradient_boosting_bias_corrector | 20553 | 0.4925 | 0.8961 | -0.0301 | 81.82% | 94.95% | 98.57% |
| holdout | min | sklearn_hist_gradient_boosting_bias_corrector | 20553 | 0.47 | 0.8178 | -0.1449 | 82.04% | 96.22% | 99.2% |
