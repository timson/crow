import aiohttp
from aiohttp_requests import requests

from . import urls
import logging
import json

_LOGGER = logging.getLogger(__name__)


class Error(Exception):
    pass


class RequestError(Error):
    pass


class CrowLoginError(Error):
    pass


class CrowWsError(Error):
    pass


class ResponseError(Error):
    def __init__(self, status_code, text):
        super(ResponseError, self).__init__(
            'Invalid response'
            ', status code: {0} - Data: {1}'.format(
                status_code,
                text))
        self.status_code = status_code
        self.text = text


def _validate_response(response):
    """ Verify that response is OK """
    if response.status == 200:
        return
    raise ResponseError(response.status, response.text)


class Panel(object):
    def __init__(self, session, mac):
        self._session = session
        self._mac = mac

    async def ainit(self):
        panel = await self._session._get_panel(self._mac)
        self.__dict__.update(panel)
        return self

    def __str__(self):
        return "{}-{} ({})".format(self.id, self.name, self.mac)

    async def get_zones(self):
        return await self._session.get_zones(self.id)

    async def get_outputs(self):
        return await self._session.get_outputs(self.id)

    async def set_output_state(self, output_id, state):
        await self._session.set_output_state(self, output_id, state)

    async def get_areas(self):
        return await self._session.get_areas(self.id)

    async def get_area(self, area_id):
        return await self._session.get_area(self.id, area_id)

    async def set_area_state(self, area_id, state):
        await self._session.set_area_state(self, area_id, state)

    async def get_measurements(self):
        return await self._session.get_measurements(self.id)

    async def get_pictures(self, zone_id, page_size=1, page=1):
        return await self._session.get_pictures(self.id, zone_id, page_size, page)


class Session(object):

    def __init__(self, email, password):
        self._email = email
        self._password = password
        self._raw_token = None
        self._token = None
        self._refresh_token = None
        self._ws_connection = None
        self.aio_session = None

    async def __aenter__(self):
        await self.login()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # self.logout()
        pass

    async def _get(self, url, params=None, retry=True):
        try:
            _headers = {
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Authorization': self._token
            }
            response = await requests.get(url, params=params, headers=_headers)
            if response.status in (400, 401):
                if retry:
                    self.login(True)
                    return self._get(url, params, retry=False)
            _validate_response(response)
        except Exception as ex:
            raise RequestError(ex)
        return await response.json()

    async def _patch(self, url, retry=True, headers=None, **kwargs):
        try:
            _headers = {
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Authorization': self._token
            }
            if headers:
                _headers.update(headers)

            response = await requests.patch(url, headers=_headers, **kwargs)
            if response.status in (400, 401):
                if retry:
                    self.login(True)
                    return self._patch(url, headers=_headers, retry=False, **kwargs)
            _validate_response(response)
        except Exception as ex:
            raise RequestError(ex)
        return await response.json()

    async def _get_panel(self, mac):
        return await self._get(urls.panel(mac))

    async def login(self, refresh=False):
        try:
            data = urls.login_data(self._email, self._password)
            if refresh and self._refresh_token:
                data = urls.refresh_data(self._refresh_token)
            response = await requests.post(urls.login(), data=data)
            _validate_response(response)
        except requests.exceptions.RequestException as ex:
            raise RequestError(ex)
        data = await response.json()
        self._raw_token = data.get('access_token')
        self._token = data.get('token_type') + ' ' + self._raw_token
        self._refresh_token = data.get('refresh_token', self._refresh_token)
        print(self._token, self._refresh_token)

    def logout(self):
        self._token = None
        self._refresh_token = None

    async def get_panels(self):
        return await self._get(urls.panels())

    async def get_panel(self, mac):
        panel = Panel(self, mac)
        return await panel.ainit()

    async def get_zones(self, panel_id):
        return await self._get(urls.zones(panel_id))

    async def get_outputs(self, panel_id):
        return await self._get(urls.outputs(panel_id))

    async def set_output_state(self, panel, output_id, state):
        return await self._patch(urls.output(panel.id, output_id),
                           json={"state": state},
                           headers={
                               "X-Crow-CP-Remote": panel.remote_access_password,
                               "X-Crow-CP-User": panel.user_code,
                           })

    async def get_areas(self, panel_id):
        return await self._get(urls.areas(panel_id))

    async def get_area(self, panel_id, area_id):
        return await self._get(urls.area(panel_id, area_id))

    async def set_area_state(self, panel, area_id, state):
        return await self._patch(urls.area(panel.id, area_id),
                           json={"state": state, "force": False},
                           headers={
                               "X-Crow-CP-Remote": panel.remote_access_password,
                               "X-Crow-CP-User": panel.user_code
                           })

    async def get_measurements(self, panel_id):
        return await self._get(urls.measurements(panel_id))

    async def get_pictures(self, panel_id, zone_id, page_size=1, page=1):
        return await self._get(urls.pictures(panel_id, zone_id, page_size, page)).get('results')

    async def download_picture(self, picture, file_name):
        url = picture.get("url")
        try:
            response = await requests.get(url, stream=True)
        except requests.exceptions.RequestException as ex:
            raise RequestError(ex)
        _validate_response(response)
        with open(file_name, 'wb') as image_file:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    image_file.write(chunk)

    async def ws_connect(self, panel, datacb):
        self.aio_session = aiohttp.ClientSession()
        self._ws_connection = await self.aio_session.ws_connect(urls.ws())
        await self._ws_connection.send_json({'type': 'authentication', 'value': self._raw_token})
        msg = await self._ws_connection.receive()
        msg = json.loads(msg.data)
        if msg['status'] == 'OK':
            print('authenticated succesfully')
        else:
            raise CrowLoginError('Authentication error: {}'.format(msg['description']))
        await self._ws_connection.send_json({'type': 'subscribe', 'value': panel})
        msg = await self._ws_connection.receive()
        msg = json.loads(msg.data)
        if msg['status'] == 'OK':
            print('Subscribed on event for panel {}'.format(panel))
        else:
            raise CrowWsError('Subscription error: {}'.format(msg['description']))

        async for msg in self._ws_connection:
            print('Received message:', msg.type)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                await datacb(data)
            if msg.type in (aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR):
                break

    async def ws_close(self):
        if not self._ws_connection:
            return
        await self._ws_connection.close()
        self._ws_connection = None
        await self.aio_session.close()
