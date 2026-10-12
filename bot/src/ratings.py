import math
from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_RATING = 1000.0
DEFAULT_DEVIATION = 350.0
DEFAULT_VOLATILITY = 0.06
MAX_DEVIATION = 350.0
MIN_RATING = 100.0
RATING_SCALE = 173.7178
SYSTEM_CONSTANT = 0.5
CONVERGENCE_TOLERANCE = 0.000001


@dataclass(frozen=True, slots=True)
class Rating:
    value: float = DEFAULT_RATING
    deviation: float = DEFAULT_DEVIATION
    volatility: float = DEFAULT_VOLATILITY


def _g(deviation: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * deviation * deviation / (math.pi**2))


def _new_volatility(
    deviation: float,
    volatility: float,
    variance: float,
    improvement: float,
) -> float:
    original = math.log(volatility * volatility)

    def score(value: float) -> float:
        exponent = math.exp(value)
        numerator = exponent * (
            improvement * improvement - deviation * deviation - variance - exponent
        )
        denominator = 2.0 * (
            deviation * deviation + variance + exponent
        ) ** 2
        return numerator / denominator - (value - original) / (SYSTEM_CONSTANT**2)

    lower = original
    if improvement * improvement > deviation * deviation + variance:
        upper = math.log(
            improvement * improvement - deviation * deviation - variance
        )
    else:
        step = 1
        while score(original - step * SYSTEM_CONSTANT) < 0:
            step += 1
        upper = original - step * SYSTEM_CONSTANT

    lower_score = score(lower)
    upper_score = score(upper)
    while abs(upper - lower) > CONVERGENCE_TOLERANCE:
        midpoint = lower + (lower - upper) * lower_score / (
            upper_score - lower_score
        )
        midpoint_score = score(midpoint)
        if midpoint_score * upper_score <= 0:
            lower = upper
            lower_score = upper_score
        else:
            lower_score /= 2.0
        upper = midpoint
        upper_score = midpoint_score

    return math.exp(lower / 2.0)


def team_rating(ratings: list[Rating]) -> Rating:
    if not ratings:
        raise ValueError("A team must contain at least one rating.")

    strongest = max(range(len(ratings)), key=lambda index: ratings[index].value)
    weights = [2.0 if index == strongest else 1.0 for index in range(len(ratings))]
    weight_total = sum(weights)
    value = sum(
        weight * rating.value for weight, rating in zip(weights, ratings)
    ) / weight_total
    deviation = math.sqrt(
        sum(
            (weight * rating.deviation) ** 2
            for weight, rating in zip(weights, ratings)
        )
    ) / weight_total
    volatility = sum(
        weight * rating.volatility for weight, rating in zip(weights, ratings)
    ) / weight_total
    return Rating(value=value, deviation=deviation, volatility=volatility)


def rating_periods(last_played_at: str | None, now: datetime) -> int:
    if not last_played_at:
        return 1
    try:
        previous = datetime.fromisoformat(last_played_at.replace("Z", "+00:00"))
    except ValueError:
        return 1
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    inactive_days = max(0, (now - previous.astimezone(timezone.utc)).days)
    return max(1, inactive_days // 7)


def update_rating(
    player: Rating,
    own_team: Rating,
    opponent_team: Rating,
    score: float,
    periods: int = 1,
) -> Rating:
    player_mean = (player.value - DEFAULT_RATING) / RATING_SCALE
    own_mean = (own_team.value - DEFAULT_RATING) / RATING_SCALE
    opponent_mean = (opponent_team.value - DEFAULT_RATING) / RATING_SCALE
    player_deviation = player.deviation / RATING_SCALE
    opponent_deviation = opponent_team.deviation / RATING_SCALE

    impact = _g(opponent_deviation)
    expected = 1.0 / (
        1.0 + math.exp(-impact * (own_mean - opponent_mean))
    )
    variance = 1.0 / (impact * impact * expected * (1.0 - expected))
    improvement = variance * impact * (score - expected)
    volatility = _new_volatility(
        player_deviation,
        player.volatility,
        variance,
        improvement,
    )

    pre_match_deviation = math.sqrt(
        player_deviation * player_deviation
        + volatility * volatility * max(1, periods)
    )
    deviation = 1.0 / math.sqrt(
        1.0 / (pre_match_deviation * pre_match_deviation) + 1.0 / variance
    )
    mean = player_mean + deviation * deviation * impact * (score - expected)

    return Rating(
        value=max(MIN_RATING, DEFAULT_RATING + RATING_SCALE * mean),
        deviation=min(MAX_DEVIATION, RATING_SCALE * deviation),
        volatility=volatility,
    )
