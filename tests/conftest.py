from __future__ import annotations

import pytest

from app.agent.tools import Toolbox, ToolState, TourRequest
from app.geo import resolve_region
from app.qloo.cache import ResponseCache
from app.qloo.client import QlooClient
from app.qloo.mock import MOCK_KEY, mock_transport


@pytest.fixture
def mock_client() -> QlooClient:
    return QlooClient(api_key=MOCK_KEY, transport=mock_transport(), cache=ResponseCache())


@pytest.fixture
def pnw_request() -> TourRequest:
    return TourRequest(
        artists=["Phoebe Bridgers", "Big Thief"],
        home_region="Pacific Northwest",
        tour_days=10,
        budget_usd=6000,
        band_size=3,
    )


@pytest.fixture
def toolbox(mock_client: QlooClient, pnw_request: TourRequest) -> Toolbox:
    state = ToolState(request=pnw_request, region=resolve_region(pnw_request.home_region))
    return Toolbox(mock_client, state)
