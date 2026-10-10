import ipaddress
import socket
from urllib.parse import urlparse


def proxies_apply(url):
    """这个地址是否应该交给系统代理（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY）。

    httpx 默认 ``trust_env=True``：环境里只要有 ``HTTP_PROXY``，连
    ``http://127.0.0.1:8088`` 这样的**本机**地址也会被送去代理。代理连不上目标时
    最常返回 502，于是真实原因（本机服务没启动／端口不对）被伪装成
    「上游返回 HTTP 502；未跟随跳转」，排查方向被直接带偏——用户会去怀疑地址填错了。

    本机与内网（回环 / 私有 / 链路本地 / 保留）地址一律直连，只有公网地址才交给系统代理：
    本机服务本来就该直连，走代理既没意义也必然失败。

    域名一律按需要代理处理：它可能只是公网加速，也可能解析到内网，无法凭字符串判断。
    """
    host = (urlparse(url).hostname or '').lower()
    if not host:
        return False
    if host in {'localhost', 'localhost.localdomain'} or host.endswith('.localhost'):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return address.is_global


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

