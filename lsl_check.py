"""Exercise the real LSL runtime and string stream used by installed capture apps."""
from uuid import uuid4


def check_lsl_roundtrip():
    import pylsl
    from lsl_markers import MarkerOutlet, STREAM_NAME, STREAM_TYPE

    sender = MarkerOutlet(source_id=f'adhd-diagnostic-{uuid4().hex}')
    assert sender.startup_error is None, sender.startup_error
    inlet = None
    try:
        streams = pylsl.resolve_byprop('source_id', sender.source_id, timeout=4)
        assert len(streams) == 1
        info = streams[0]
        assert info.name() == STREAM_NAME and info.type() == STREAM_TYPE
        assert info.channel_count() == 1 and info.channel_format() == pylsl.cf_string
        assert info.nominal_srate() == pylsl.IRREGULAR_RATE
        inlet = pylsl.StreamInlet(info, recover=False)
        inlet.open_stream(timeout=4)
        assert sender.wait_for_consumers(4), 'LSL inlet did not finish subscribing'
        before = pylsl.local_clock()
        assert sender.emit('CALIBRATION_1_START')
        sample, timestamp = inlet.pull_sample(timeout=4)
        assert sample == ['CALIBRATION_1_START'] and before <= timestamp <= pylsl.local_clock()
    finally:
        if inlet is not None:
            inlet.close_stream()
        sender.close()
