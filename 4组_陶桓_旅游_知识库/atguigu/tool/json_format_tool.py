"""
用于将输出的state或者其他格式转换成josn,方便输出观看
"""

import json

from bson import ObjectId


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)
def json_format(data):
    return json.dumps(data,indent=4,ensure_ascii=False,cls=NumpyEncoder)