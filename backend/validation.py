import math
import re
from datetime import datetime, timezone, timedelta

class ValidationError(ValueError):
    pass

def identifier(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise ValidationError(f'{field} must contain 1–64 letters, digits, underscores or hyphens')
    return value

def number(data, key, low, high):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or not math.isfinite(value):
        raise ValidationError(f'{key} must be a finite number between {low} and {high}')
    return float(value)

def object_body(data):
    if not isinstance(data, dict):
        raise ValidationError('JSON body must be an object')

def reading(data):
    object_body(data)
    expected = {'package_id', 'reading_id', 'seq', 'timestamp', 'temperature', 'humidity', 'distance', 'accel_mps2', 'gyro_radps', 'source_status'}
    if set(data) != expected:
        raise ValidationError('Required fields: ' + ', '.join(sorted(expected)))
    result = {key: identifier(data.get(key), key) for key in ('package_id', 'reading_id')}
    seq = data.get('seq')
    if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq <= 4294967295:
        raise ValidationError('seq must be an unsigned 32-bit integer')
    result['seq'] = seq
    result['source_status'] = identifier(data.get('source_status'), 'source_status')
    try:
        stamp = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        stamp = stamp.astimezone(timezone.utc)
        if stamp > datetime.now(timezone.utc) + timedelta(minutes=5) or stamp.year < 2020:
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise ValidationError('timestamp must be an ISO-8601 time with timezone, from 2020 onward and no more than 5 minutes in the future')
    result['timestamp'] = stamp.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    for key, low, high in [('temperature', -40, 125), ('humidity', 0, 100), ('distance', 0, 500)]:
        result[key] = number(data, key, low, high)
    result['accel_mps2'] = number(data, 'accel_mps2', 0, 280)
    result['gyro_radps'] = number(data, 'gyro_radps', 0, 61)
    return result

def package(data):
    object_body(data)
    allowed = {'id', 'name', 'origin', 'destination', 'min_temp', 'max_temp', 'accel_threshold', 'gyro_threshold'}
    if set(data) - allowed:
        raise ValidationError('Unknown package field')
    result = {'id': identifier(data.get('id'), 'id')}
    for key in ('name', 'origin', 'destination'):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValidationError(f'{key} must be a nonempty string up to 160 characters')
        result[key] = value.strip()
    defaults = {'min_temp': 2, 'max_temp': 30, 'accel_threshold': 8, 'gyro_threshold': 3.14}
    values = defaults | data
    for key, low, high in [('min_temp', -40, 125), ('max_temp', -40, 125), ('accel_threshold', .1, 280), ('gyro_threshold', .01, 61)]:
        result[key] = number(values, key, low, high)
    if result['min_temp'] >= result['max_temp']:
        raise ValidationError('min_temp must be less than max_temp')
    return result
