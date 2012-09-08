import requests
import base64
import json
import urllib

class APIError(Exception):
    pass

class Client(object):
    def __init__(self, base_url, api_key, async=False, config={}):
        assert base_url
        self.base_url = base_url
        self.api_key = api_key
        self.async = async
        self.config = config
        if async:
            import grequests
            self.requests = grequests
        else:
            self.requests = requests.session()

    def dispatch(self, method_name, endpoint, data=None):
        method = getattr(self.requests, method_name)

        response = method(
            self.base_url+endpoint,
            data=data != None and json.dumps(data),
            headers={'Authorization' : 'Basic %s' % (base64.b64encode('%s:' % self.api_key), ), 'Content-Type': 'application/json'},
            config=self.config
        )

        if self.async:
            return response
        else:
            if response.ok:
                return json.loads(response.content)
            else:
                print response, response.content
                raise APIError()

    def get(self, endpoint, data=None):
        if data:
            endpoint += '/?'+urllib.urlencode(data)
        else:
            endpoint += '/'
        return self.dispatch('get', endpoint)

    def post(self, endpoint, data):
        return self.dispatch('post', endpoint+'/', data)

    def put(self, endpoint, data):
        return self.dispatch('put', endpoint+'/', data)

    # Only for async requests
    def map(self, reqs, max_retries=5):
        # TODO
        # There is no good way of catching or dealing with exceptions that are raised
        # during the request sending process when using map or imap.
        # When this issue is closed: https://github.com/kennethreitz/grequests/pull/15
        # modify this method to repeat only the requests that failed because of
        # connection errors
        if self.async:
            import grequests
            responses = [(
                json.loads(response.content) if response.ok and response.content is not None
                else APIError()
            ) for response in grequests.map(reqs)]
            # retry the api calls that failed until they succeed or the max_retries limit is reached
            retries = 0
            while True and retries < max_retries:
                n_errors = reduce(lambda x, y: x + int(isinstance(y, APIError)), responses, 0)
                if not n_errors:
                    break
                error_ids = [i for i in range(len(responses)) if isinstance(responses[i], APIError)]
                new_reqs = [reqs[i] for i in range(len(responses)) if i in error_ids]
                new_resps = [(
                    json.loads(response.content) if response.ok and response.content is not None
                    else APIError()
                ) for response in grequests.map(new_reqs)]
                # update the responses that previously finished with errors
                for i in range(len(error_ids)):
                    responses[error_ids[i]] = new_resps[i]
                retries += 1
            return responses
