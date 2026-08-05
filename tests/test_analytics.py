from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from solar_analytics import (
    DailyMetric,
    IntervalMetric,
    SolarAnalyticsStore,
    ValidityContext,
    aggregate_power_samples,
    build_consensus,
    compute_accuracy,
    compute_baseline,
    detect_anomalies,
    evaluate_validity,
    normalize_forecast_result,
    resample_forecast,
)
from solar_analytics.analytics import KYIV, forecast_profile_analysis_allowed, forecast_snapshot_is_admissible, to_w

UTC = timezone.utc


class ForecastNormalizationTests(unittest.TestCase):
    def test_unit_conversion_is_dimensional(self) -> None:
        self.assertEqual(to_w(2, "kW"), 2000)
        self.assertEqual(to_w(0.5, "kWh", 1800), 1000)
        self.assertEqual(to_w(500, "Wh", 1800), 1000)

    def test_observed_forecast_metadata_mismatch_is_preserved(self) -> None:
        raw = {
            "result": {
                "2026-08-02T10:00:00+03:00": 3724,
                "2026-08-02T10:30:00+03:00": 3724,
                "2026-08-02T11:00:00+03:00": 4201,
            }
        }
        normalized = normalize_forecast_result(
            raw,
            provider="forecast_solar",
            declared_unit="kWh",
            effective_unit="W",
            value_semantics="power",
        )
        self.assertEqual(normalized.contract_status, "metadata_mismatch")
        self.assertEqual(normalized.points[0].power_w, 3724)
        self.assertEqual(normalized.points[0].energy_kwh_30m, 1.862)

    def test_duplicate_timestamps_are_last_write_wins_and_counted(self) -> None:
        raw = [
            {"timestamp": "2026-08-02T10:00:00+03:00", "value": 1000},
            {"timestamp": "2026-08-02T10:00:00+03:00", "value": 1200},
        ]
        normalized = normalize_forecast_result(raw, provider="forecast_solar")
        self.assertEqual(normalized.duplicate_timestamps, 1)
        self.assertEqual(normalized.points[0].power_w, 1200)

    def test_period_energy_requires_timestamp_semantics(self) -> None:
        raw = {
            "2026-08-02T05:27:00+03:00": 0,
            "2026-08-02T06:00:00+03:00": 115,
            "2026-08-02T07:00:00+03:00": 861,
        }
        blocked = normalize_forecast_result(
            raw,
            provider="forecast_solar",
            declared_unit="kWh",
            effective_unit="Wh",
            value_semantics="energy",
        )
        self.assertEqual(blocked.contract_status, "blocked_timestamp_semantics")
        self.assertEqual(blocked.points, ())

        period_end = normalize_forecast_result(
            raw,
            provider="forecast_solar",
            declared_unit="kWh",
            effective_unit="Wh",
            value_semantics="energy",
            timestamp_semantics="end",
        )
        self.assertEqual(period_end.contract_status, "metadata_mismatch")
        self.assertEqual(len(period_end.points), 2)
        self.assertEqual(period_end.points[0].timestamp.hour, 5)
        self.assertAlmostEqual(period_end.points[0].power_w, 115 * 3600 / (33 * 60), places=3)
        self.assertEqual(period_end.invalid_points, 1)

    def test_resampling_handles_dst_on_utc_grid(self) -> None:
        points = normalize_forecast_result(
            {
                "2026-03-29T00:00:00+02:00": 1000,
                "2026-03-29T03:00:00+03:00": 2000,
            },
            provider="forecast_solar",
        )
        result = resample_forecast(
            points,
            datetime(2026, 3, 29, 0, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
            datetime(2026, 3, 29, 6, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
        )
        # The skipped local 03:00 hour is not fabricated; elapsed time is 5 hours.
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0].timestamp.utcoffset(), timedelta(hours=2))
        self.assertEqual(result[-1].timestamp.utcoffset(), timedelta(hours=3))
    def test_profile_analysis_gate_fails_closed_for_native_contract_ambiguity(self) -> None:
        native = {"status": "ok", "modules_power_w": 5360.0, "inverter_size_w": 5190.0}
        aligned = {
            "model_status": "aligned_to_native",
            "contract_status": "metadata_mismatch",
            "normalization_blocked": False,
        }
        self.assertTrue(forecast_profile_analysis_allowed(native, aligned))
        self.assertFalse(
            forecast_profile_analysis_allowed(
                native,
                {**aligned, "model_status": "blocked_model_mismatch"},
            )
        )
        self.assertFalse(
            forecast_profile_analysis_allowed(
                native,
                {**aligned, "contract_status": "blocked_timestamp_semantics", "normalization_blocked": True},
            )
        )
        self.assertFalse(
            forecast_profile_analysis_allowed(
                {"status": "missing_or_ambiguous"},
                {**aligned, "model_status": "native_contract_unavailable"},
            )
        )

    def test_blocked_forecast_snapshot_is_quarantined_from_daily_metrics(self) -> None:
        blocked = {
            "provider": "forecast_solar",
            "profile_status": "complete",
            "quality": {
                "model_status": "native_contract_unavailable",
                "normalization_blocked": True,
            },
        }
        self.assertFalse(forecast_snapshot_is_admissible(blocked))
        self.assertFalse(
            forecast_snapshot_is_admissible(
                {"provider": "forecast_solar", "profile_status": "complete", "quality": {}}
            )
        )
        self.assertTrue(
            forecast_snapshot_is_admissible(
                {
                    "provider": "forecast_solar",
                    "profile_status": "complete",
                    "quality": {
                        "model_status": "aligned_to_native",
                        "normalization_blocked": False,
                    },
                }
            )
        )


class AggregationAndValidityTests(unittest.TestCase):
    def test_time_weighted_30_minute_average(self) -> None:
        samples = [
            (datetime(2026, 8, 2, 10, 0, tzinfo=KYIV), 1000),
            (datetime(2026, 8, 2, 10, 15, tzinfo=KYIV), 2000),
            (datetime(2026, 8, 2, 10, 30, tzinfo=KYIV), 2000),
        ]
        aggregate = aggregate_power_samples(
            samples,
            datetime(2026, 8, 2, 10, 0, tzinfo=KYIV),
            datetime(2026, 8, 2, 10, 30, tzinfo=KYIV),
        )[0]
        self.assertAlmostEqual(aggregate.actual_power_average_w or 0, 1500)
        self.assertAlmostEqual(aggregate.actual_energy_kwh or 0, 0.75)
        self.assertEqual(aggregate.coverage_ratio, 1.0)

    def test_gap_is_not_converted_to_zero(self) -> None:
        aggregate = aggregate_power_samples(
            [
                (datetime(2026, 8, 2, 10, 0, tzinfo=KYIV), 1000),
                (datetime(2026, 8, 2, 10, 29, tzinfo=KYIV), 1000),
            ],
            datetime(2026, 8, 2, 10, 0, tzinfo=KYIV),
            datetime(2026, 8, 2, 10, 30, tzinfo=KYIV),
            max_gap_seconds=60,
        )[0]
        self.assertLess(aggregate.coverage_ratio, 0.8)
        self.assertEqual(aggregate.data_quality, "gap")

    def test_curtailment_is_invalid_but_not_a_fault(self) -> None:
        result = evaluate_validity(
            ValidityContext(
                actual_power_w=100,
                expected_power_w=1500,
                battery_full=True,
                load_or_export_available=False,
            )
        )
        self.assertFalse(result.analysis_valid)
        self.assertEqual(result.reason, "battery_full")
        self.assertEqual(result.curtailment_reason, "battery_full")

    def test_mppt_error_and_missing_forecast_are_distinct(self) -> None:
        error = evaluate_validity(ValidityContext(actual_power_w=100, expected_power_w=1500, mppt_error="fault"))
        missing = evaluate_validity(ValidityContext(actual_power_w=100, expected_power_w=None))
        self.assertEqual(error.reason, "mppt_error")
        self.assertEqual(missing.reason, "forecast_unavailable")


class AccuracyAndAnomalyTests(unittest.TestCase):
    def test_anomaly_detection_fails_closed_without_native_capacity(self) -> None:
        result = detect_anomalies([], [], baseline=None, array_capacity_w=None, inverter_size_w=None)
        self.assertEqual(result["classification"], "forecast_contract_unavailable")
        self.assertFalse(result["near_zero_anomaly"])
        self.assertFalse(result["clipping_detected"])
        self.assertEqual(result["evidence"]["capacity_contract"], "unavailable")

    def _day(self, day: int, actual: float, solar: float, vrm: float, coverage: float = 1.0, **kwargs: object) -> DailyMetric:
        return DailyMetric(
            local_date=date(2026, 8, day),
            actual_valid_energy_kwh=actual,
            actual_total_energy_kwh=actual,
            forecast_solar_kwh=solar,
            vrm_forecast_kwh=vrm,
            valid_coverage=coverage,
            valid_intervals=48,
            expected_intervals=48,
            **kwargs,
        )

    def test_bias_and_wape_match_requested_formulas(self) -> None:
        days = [self._day(1, 8, 10, 10), self._day(2, 18, 20, 20)]
        metrics = compute_accuracy(days, "forecast_solar", 30)
        self.assertAlmostEqual(metrics.bias or 0, -4 / 30)
        self.assertAlmostEqual(metrics.wape or 0, 4 / 26)

    def test_consensus_weights_are_equal_before_minimum_history_and_bounded_after(self) -> None:
        equal = build_consensus(10, 20, solar_wape=0.01, vrm_wape=0.5, valid_days=13)
        self.assertEqual(equal["weights"], {"forecast_solar": 0.5, "vrm": 0.5})
        bounded = build_consensus(10, 20, solar_wape=0.01, vrm_wape=0.5, valid_days=30)
        self.assertGreaterEqual(bounded["weights"]["vrm"], 0.2)
        self.assertLessEqual(bounded["weights"]["vrm"], 0.8)

    def test_baseline_excludes_curtailment(self) -> None:
        days = [self._day(1, 9, 10, 10), self._day(2, 2, 10, 10, curtailment_duration_minutes=30), self._day(3, 8, 10, 10)]
        self.assertAlmostEqual(compute_baseline(days) or 0, 0.85)

    def test_near_zero_requires_valid_uncurtailed_interval(self) -> None:
        interval = IntervalMetric(
            interval_start=datetime(2026, 8, 2, 10, 0, tzinfo=KYIV),
            actual_power_average_w=100,
            actual_energy_kwh=0.05,
            forecast_solar_power_w=1500,
            forecast_solar_energy_kwh=0.75,
            vrm_forecast_power_w=1500,
            vrm_forecast_energy_kwh=0.75,
            consensus_expected_power_w=1500,
            consensus_expected_energy_kwh=0.75,
            analysis_valid=True,
        )
        flags = detect_anomalies([interval], [], baseline=None, array_capacity_w=4920)
        self.assertTrue(flags["near_zero_anomaly"])
        curtailed = interval.__class__(**{**interval.__dict__, "curtailment_reason": "battery_full"})
        self.assertFalse(detect_anomalies([curtailed], [], baseline=None, array_capacity_w=4920)["near_zero_anomaly"])

    def test_provider_winner_and_strong_disagreement_are_reportable(self) -> None:
        sunny = [self._day(1, 10, 10, 12), self._day(2, 20, 20, 24)]
        solar = compute_accuracy(sunny, "forecast_solar", 30)
        vrm = compute_accuracy(sunny, "vrm", 30)
        self.assertLess(solar.wape if solar.wape is not None else 1, vrm.wape if vrm.wape is not None else 1)
        disagree = [self._day(3, 10, 4, 20), self._day(4, 10, 4, 20)]
        solar_disagree = compute_accuracy(disagree, "forecast_solar", 30)
        vrm_disagree = compute_accuracy(disagree, "vrm", 30)
        self.assertGreater(abs((solar_disagree.bias or 0) - (vrm_disagree.bias or 0)), 1.0)

    def test_bms_dvcc_external_mppt_and_missing_data_reasons(self) -> None:
        contexts = [
            (ValidityContext(actual_power_w=100, expected_power_w=1500, battery_can_accept_charge=False), "bms_charge_limit"),
            (ValidityContext(actual_power_w=100, expected_power_w=1500, dvcc_limit_active=True), "dvcc_limit"),
            (ValidityContext(actual_power_w=100, expected_power_w=1500, mppt_external_control=True), "external_control"),
            (ValidityContext(actual_power_w=100, expected_power_w=1500, mppt_error="charger_over_current"), "mppt_error"),
            (ValidityContext(actual_power_w=None, expected_power_w=1500), "sensor_unavailable"),
            (ValidityContext(actual_power_w=100, expected_power_w=None), "forecast_unavailable"),
        ]
        for context, reason in contexts:
            with self.subTest(reason=reason):
                self.assertEqual(evaluate_validity(context).reason, reason)

    def test_clipping_is_an_annotation_not_a_fault(self) -> None:
        records = [
            IntervalMetric(
                interval_start=datetime(2026, 8, 2, 10 + index // 2, (index % 2) * 30, tzinfo=KYIV),
                actual_power_average_w=5500,
                actual_energy_kwh=2.75,
                forecast_solar_power_w=6000,
                forecast_solar_energy_kwh=3,
                vrm_forecast_power_w=5900,
                vrm_forecast_energy_kwh=2.95,
                consensus_expected_power_w=5950,
                consensus_expected_energy_kwh=2.975,
                analysis_valid=True,
            )
            for index in range(3)
        ]
        flags = detect_anomalies(records, [], baseline=None, array_capacity_w=4920, inverter_size_w=5500)
        self.assertTrue(flags["clipping_detected"])
        self.assertEqual(flags["classification"], "clipping_detected")

    def test_step_change_requires_multiple_days(self) -> None:
        days = [self._day(day, 10, 10, 10) for day in range(1, 15)]
        days.extend(self._day(day, 7, 10, 10) for day in range(15, 18))
        flags = detect_anomalies([], days, baseline=1.0, array_capacity_w=4920)
        self.assertTrue(flags["step_change"])


class StorageTests(unittest.TestCase):
    def test_store_allows_executor_thread_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SolarAnalyticsStore(Path(directory) / "solar.sqlite")
            store.set_runtime("main", {"value": 1})
            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    store.set_runtime("worker", {"value": 2})
                except BaseException as exc:  # pragma: no cover - assertion below
                    errors.append(exc)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(store.get_runtime("worker"), 2)
            store.close()

    def test_time_weighted_accumulator_survives_restart_and_uses_utc_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "solar.sqlite"
            first = SolarAnalyticsStore(path)
            first.add_power_sample(datetime(2026, 8, 2, 7, 0, tzinfo=UTC), 1000)
            first.add_power_sample(datetime(2026, 8, 2, 7, 15, tzinfo=UTC), 2000)
            first.close()
            second = SolarAnalyticsStore(path)
            row = second.get_accumulator("2026-08-02T07:00:00+00:00")
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row["actual_energy_kwh"], 0.25)
            self.assertAlmostEqual(row["actual_power_average_w"], 1000)
            self.assertLess(row["coverage_ratio"], 1.0)
            second.close()

    def test_snapshot_key_is_idempotent_across_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "solar.sqlite"
            first = SolarAnalyticsStore(path)
            inserted = first.upsert_snapshot(
                provider="forecast_solar",
                target_date=date(2026, 8, 3),
                snapshot_type="day_ahead",
                snapshot_timestamp=datetime(2026, 8, 2, 20, tzinfo=KYIV),
                daily_energy_kwh=25.0,
                profile={"points": [["2026-08-03T06:00:00+03:00", 1000]]},
                parameters={"azimuth": 137.7},
                source_id="forecast_solar_entry",
                profile_status="complete",
            )
            first.close()
            second = SolarAnalyticsStore(path)
            duplicate = second.upsert_snapshot(
                provider="forecast_solar",
                target_date=date(2026, 8, 3),
                snapshot_type="day_ahead",
                snapshot_timestamp=datetime(2026, 8, 2, 20, 1, tzinfo=KYIV),
                daily_energy_kwh=26.0,
                profile={"changed": True},
                parameters={},
                source_id="forecast_solar_entry",
                profile_status="complete",
            )
            self.assertTrue(inserted)
            self.assertFalse(duplicate)
            snapshot = second.get_snapshot("forecast_solar", date(2026, 8, 3), "day_ahead")
            self.assertEqual(snapshot["daily_energy_kwh"], 25.0)
            self.assertEqual(snapshot["profile"]["points"][0][1], 1000)
            second.close()


if __name__ == "__main__":
    unittest.main()
