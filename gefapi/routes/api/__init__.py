# GENERIC Error

from flask import jsonify

# GENERIC Error


def error(status=400, detail='Bad Request'):
    return jsonify({'status': status, 'detail': detail}), status
