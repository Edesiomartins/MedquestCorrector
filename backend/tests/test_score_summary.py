from app.services.grading.score_summary import points_from_fraction, summarize_scores


def test_seven_of_ten_without_pending_is_seven_points():
    items = [{"score": 1, "max_score": 1, "pending": False} for _ in range(7)]
    items += [{"score": 0, "max_score": 1, "pending": False} for _ in range(3)]
    summary = summarize_scores(items)
    assert summary["confirmed_points"] == 7
    assert summary["final_score"] == 7
    assert summary["max_points"] == 10
    assert summary["pending_count"] == 0
    assert summary["status"] == "concluida"


def test_six_correct_and_one_pending_stays_provisional():
    items = [{"score": 1, "max_score": 1, "pending": False} for _ in range(6)]
    items.append({"score": None, "max_score": 1, "pending": True})
    items += [{"score": 0, "max_score": 1, "pending": False} for _ in range(3)]
    summary = summarize_scores(items)
    assert summary["confirmed_points"] == 6
    assert summary["pending_count"] == 1
    assert summary["final_score"] is None
    assert summary["status"] == "provisoria"
    assert summary["max_points"] == 10


def test_scale_uses_max_points_only_when_concluded():
    items = [{"score": 7, "max_score": 10, "pending": False}]
    open_summary = summarize_scores(
        [{"score": 6, "max_score": 10, "pending": False}, {"score": None, "max_score": 0, "pending": True}],
        scale_max=10,
    )
    assert open_summary["proportional_score"] is None
    closed = summarize_scores(items, scale_max=10)
    assert closed["proportional_score"] == 7


def test_discursive_fraction_times_question_value():
    assert points_from_fraction(1, 2) == 2
    assert points_from_fraction(0.5, 2) == 1
    assert points_from_fraction(0.25, 4) == 1
    assert points_from_fraction(None, 2) is None
