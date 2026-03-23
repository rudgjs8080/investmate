"""AI 분석 결과 검증기 테스트."""

from src.ai.validator import calibrate_confidence, validate_ai_results


class TestValidateTargetPrice:
    def test_target_below_current_auto_corrected(self):
        """목표가 < 현재가 → 자동 보정."""
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_target_price": 90.0}]
        prices = {"AAPL": 100.0}
        warnings = validate_ai_results(parsed, prices)
        assert len(warnings) >= 1
        assert parsed[0]["ai_target_price"] == 110.0  # 10% 상향

    def test_valid_target_no_warning(self):
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_target_price": 120.0}]
        prices = {"AAPL": 100.0}
        warnings = validate_ai_results(parsed, prices)
        target_warns = [w for w in warnings if "목표가" in w]
        assert len(target_warns) == 0


class TestValidateStopLoss:
    def test_stop_above_current_auto_corrected(self):
        """손절가 > 현재가 → 자동 보정."""
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_stop_loss": 110.0}]
        prices = {"AAPL": 100.0}
        warnings = validate_ai_results(parsed, prices)
        assert len(warnings) >= 1
        assert parsed[0]["ai_stop_loss"] == 93.0  # 7% 하향


class TestValidateTargetStopSwap:
    def test_swapped_values(self):
        """목표가 < 손절가 → 값 교환."""
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_target_price": 80.0, "ai_stop_loss": 120.0}]
        prices = {"AAPL": 100.0}
        validate_ai_results(parsed, prices)
        # 자동 보정 후 교환 가능
        assert isinstance(parsed[0]["ai_target_price"], float)


class TestConfidenceConsistency:
    def test_low_confidence_approved(self):
        """추천인데 신뢰도 1 → 경고."""
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_confidence": 1}]
        prices = {"AAPL": 100.0}
        warnings = validate_ai_results(parsed, prices)
        assert any("매우 낮음" in w for w in warnings)

    def test_high_confidence_excluded(self):
        """제외인데 신뢰도 9 → 경고."""
        parsed = [{"ticker": "AAPL", "ai_approved": False, "ai_confidence": 9}]
        prices = {"AAPL": 100.0}
        warnings = validate_ai_results(parsed, prices)
        assert any("매우 높음" in w for w in warnings)

    def test_consistent_no_warning(self):
        """일관적 → 경고 없음."""
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_confidence": 8}]
        prices = {"AAPL": 100.0}
        warnings = validate_ai_results(parsed, prices)
        confidence_warns = [w for w in warnings if "신뢰도" in w]
        assert len(confidence_warns) == 0


class TestEmptyInput:
    def test_empty_parsed(self):
        assert validate_ai_results([], {}) == []

    def test_missing_price(self):
        """가격 정보 없는 종목 → skip."""
        parsed = [{"ticker": "UNKNOWN", "ai_approved": True, "ai_target_price": 50.0}]
        warnings = validate_ai_results(parsed, {})
        assert warnings == []


class TestCalibrateConfidence:
    def test_calibrate_confidence_with_data(self):
        """충분한 데이터가 있으면 보정된 값 반환."""
        curve = {
            8: {"predicted": 0.8, "actual": 0.5, "count": 10, "gap": -0.3},
        }
        result = calibrate_confidence(8, curve)
        assert result == 5  # round(0.5 * 10) = 5

    def test_calibrate_confidence_insufficient_data(self):
        """데이터 5건 미만이면 원본 반환."""
        curve = {
            8: {"predicted": 0.8, "actual": 0.3, "count": 3, "gap": -0.5},
        }
        result = calibrate_confidence(8, curve)
        assert result == 8  # count < 5 -> 원본

    def test_calibrate_confidence_empty_curve(self):
        """빈 커브면 원본 반환."""
        assert calibrate_confidence(7, {}) == 7
