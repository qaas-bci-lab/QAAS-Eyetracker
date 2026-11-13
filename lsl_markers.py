"""LSL outlet for experiment timing markers."""
import logging
import socket

from app_paths import APP_EDITION


STREAM_NAME = 'EyeTracker_Markers'
STREAM_TYPE = 'Markers'


class MarkerOutlet:
    def __init__(self, *, source_id=None):
        self.source_id = source_id or f'eyetracker:{socket.gethostname()}:{APP_EDITION}'
        self.startup_error = None
        self._outlet = None
        try:
            import pylsl
            info = pylsl.StreamInfo(STREAM_NAME, STREAM_TYPE, 1, pylsl.IRREGULAR_RATE,
                                    pylsl.cf_string, self.source_id)
            channels = info.desc().append_child('channels')
            channels.append_child('channel').append_child_value('label', 'trigger')
            self._outlet = pylsl.StreamOutlet(info, chunk_size=1)
            self._clock = pylsl.local_clock
        except Exception as error:
            self.startup_error = str(error)
            logging.getLogger(__name__).exception('Unable to open the LSL marker outlet')

    def emit(self, code, *, session_id=None, segment_index=None):
        if self._outlet is None:
            return False
        try:
            self._outlet.push_sample([code], timestamp=self._clock(), pushthrough=True)
            return True
        except Exception:
            logging.getLogger(__name__).exception('Could not send LSL marker %s (experiment %s, segment %s)',
                                                  code, session_id, segment_index)
            return False

    def close(self):
        self._outlet = None

    def wait_for_consumers(self, timeout):
        return self._outlet is not None and self._outlet.wait_for_consumers(timeout)
