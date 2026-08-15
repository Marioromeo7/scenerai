"""Tests that ai_service.py's turn/init entry points actually call the Groq
TPM reservation gate before doing any engine work — rate_limiter.py itself
is already tested standalone (test_rate_limiter.py); this covers the
integration point that was previously missing."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import ai_service


class TestReserveTurnBudget:
    @pytest.mark.asyncio
    async def test_raises_on_capacity_exhausted_not_silent(self):
        with patch('ai_service.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('ai_service.reserve_token_budget', new=AsyncMock(return_value=False)):
            with pytest.raises(RuntimeError, match="fully booked"):
                await ai_service._reserve_turn_budget()

    @pytest.mark.asyncio
    async def test_returns_normally_when_budget_available(self):
        with patch('ai_service.get_redis', new=AsyncMock(return_value=MagicMock())), \
             patch('ai_service.reserve_token_budget', new=AsyncMock(return_value=True)) as mock_reserve:
            await ai_service._reserve_turn_budget(estimated_tokens=1234)
        mock_reserve.assert_awaited_once()
        assert mock_reserve.call_args.args[1] == 1234


class TestEngineStepCallsReservationFirst:
    @pytest.mark.asyncio
    async def test_capacity_exhausted_blocks_before_touching_engine(self):
        """The engine must never run (no wasted work, no partial state
        mutation) if the reservation itself fails."""
        with patch('ai_service._reserve_turn_budget', new=AsyncMock(side_effect=RuntimeError("fully booked"))), \
             patch('ai_service.deserialize_engine') as mock_deserialize:
            with pytest.raises(RuntimeError, match="fully booked"):
                await ai_service.engine_step({"fake": "state"}, "hello")
        mock_deserialize.assert_not_called()

    @pytest.mark.asyncio
    async def test_reservation_happens_before_deserialize(self):
        call_order = []

        async def fake_reserve(*a, **kw):
            call_order.append('reserve')

        def fake_deserialize(state):
            call_order.append('deserialize')
            engine = MagicMock()
            engine.step = MagicMock(return_value={
                'response': 'x', 'sovereign': True, 'violations': [], 'turn': 1,
            })
            return engine

        with patch('ai_service._reserve_turn_budget', side_effect=fake_reserve), \
             patch('ai_service.deserialize_engine', side_effect=fake_deserialize), \
             patch('ai_service.serialize_engine', return_value={}), \
             patch('ai_service.init_client'):
            await ai_service.engine_step({"fake": "state"}, "hello")

        assert call_order == ['reserve', 'deserialize']


class TestEngineRegenerateCallsReservationFirst:
    @pytest.mark.asyncio
    async def test_capacity_exhausted_blocks_before_touching_engine(self):
        with patch('ai_service._reserve_turn_budget', new=AsyncMock(side_effect=RuntimeError("fully booked"))), \
             patch('ai_service.deserialize_engine') as mock_deserialize:
            with pytest.raises(RuntimeError, match="fully booked"):
                await ai_service.engine_regenerate({"fake": "state"})
        mock_deserialize.assert_not_called()


class TestEnginePrefabCallsReservationFirst:
    @pytest.mark.asyncio
    async def test_capacity_exhausted_blocks_before_scenario_build(self):
        with patch('ai_service.settings') as mock_settings, \
             patch('ai_service._reserve_turn_budget', new=AsyncMock(side_effect=RuntimeError("fully booked"))) as mock_reserve, \
             patch('ai_service.scenario_to_engine') as mock_build:
            mock_settings.groq_api_key = 'fake-key'
            with pytest.raises(RuntimeError, match="fully booked"):
                await ai_service.engine_prefab('Name', 'they/them', 'Title', 'Personality', 'Hi')
        mock_build.assert_not_called()
        # scaled-up estimate for the 15-20 internal calls, not the per-turn one
        assert mock_reserve.call_args.args[0] == ai_service.FULL_INIT_TOKEN_ESTIMATE
