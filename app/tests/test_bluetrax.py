import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# from app.bluetrax import authenticate, get_assets, get_asset_history

class AsyncContextManagerMock(MagicMock):
    async def __aenter__(self):
        return self.aenter

    async def __aexit__(self, *args):
        pass

@pytest.mark.asyncio
async def test_authenticate(mocker):

    mock_session = AsyncMock()
    mock_session.execute.return_value = {'selectUsersByUsernamePassword': [{'user_id': '1', 'client_id': '1', 'contact_id': '1', 'client_name': 'Test Client'}]}

    with patch('gql.client.AsyncClientSession', return_value=mock_session):
        from app.bluetrax import authenticate

        result = await authenticate("testuser", "testpassword")

    assert result.users[0].user_id == "1"
    assert result.users[0].client_id == "1"
    assert result.users[0].contact_id == "1"
    assert result.users[0].client_name == "Test Client"

@pytest.mark.asyncio
async def test_authenticate_with_empty_result(mocker):

    mock_session = AsyncMock()
    mock_session.execute.return_value = {'selectUsersByUsernamePassword': []}

    with patch('gql.client.AsyncClientSession', return_value=mock_session):
        from app.bluetrax import authenticate

        result = await authenticate("testuser", "testpassword")

    assert len(result.users) == 0


