import ipaddress
import socket
from urllib.parse import urlparse

def validate_url(url,allow_localhost=False):
    parsed=urlparse(url)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请填写不含密码或查询参数的语音服务基础地址。')
    if parsed.scheme!='https' and not allow_localhost:
        raise ValueError('远程语音服务须使用 HTTPS；本机服务须明确启用本机访问。')
    addresses=socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)
    for item in addresses:
        address=ipaddress.ip_address(item[4][0])
        if parsed.scheme=='http' and not (allow_localhost and address.is_loopback):
            raise ValueError('远程语音服务须使用 HTTPS。')
        if not address.is_global and not (allow_localhost and address.is_loopback):
            raise ValueError('语音服务不能访问局域网或保留地址。')

