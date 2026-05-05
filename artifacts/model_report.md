# Temperature Model Report

- model: `sklearn_hist_gradient_boosting_bias_corrector`
- training rows: `3100`
- holdout rows: `1100`
- train period: `2026-03-01` to `2026-03-20`
- holdout period: `2026-03-21` to `2026-03-31`
- features: `target_day_of_year_sin, target_day_of_year_cos, target_month, target_is_weekend, target_is_max, station_latitude, station_longitude, station_elevation_m, station_id_hash, source_domain_hash, forecast_temp_max_c, forecast_temp_min_c, forecast_temp_mean_c, forecast_temp_spread_c`

| split | group | model | samples | MAE C | RMSE C | bias C | within 1C | within 2C | within 3C |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| holdout | combined | nwp_daily_extreme_baseline | 1100 | 0.5786 | 1.2666 | 0.0541 | 80.36% | 94.36% | 97.36% |
| holdout | max | nwp_daily_extreme_baseline | 550 | 0.6904 | 1.5793 | -0.0762 | 78.0% | 92.0% | 96.0% |
| holdout | min | nwp_daily_extreme_baseline | 550 | 0.4669 | 0.8451 | 0.1844 | 82.73% | 96.73% | 98.73% |
| holdout | combined | sklearn_hist_gradient_boosting_bias_corrector | 1100 | 0.5063 | 0.9165 | -0.0732 | 84.09% | 96.55% | 98.18% |
| holdout | max | sklearn_hist_gradient_boosting_bias_corrector | 550 | 0.5371 | 1.0222 | -0.1105 | 84.73% | 96.0% | 97.45% |
| holdout | min | sklearn_hist_gradient_boosting_bias_corrector | 550 | 0.4754 | 0.7969 | -0.036 | 83.45% | 97.09% | 98.91% |
