# TLS 证书部署指南

本文档说明如何为灵境制造系统配置 TLS 证书以启用 HTTPS。

## 目录结构

```
deploy/nginx/certs/
├── fullchain.pem    # 完整证书链（服务器证书 + 中间证书）
├── privkey.pem      # 私钥文件
└── README_TLS.md    # 本文件
```

## 获取证书

### 方式一：使用 Let's Encrypt（推荐，免费）

使用 Certbot 自动获取和续期证书：

```bash
# 安装 Certbot
sudo apt update
sudo apt install certbot python3-certbot-nginx

# 获取证书（确保域名已解析到服务器 IP）
sudo certbot --nginx -d your-domain.com

# 证书将自动存储在 /etc/letsencrypt/live/your-domain.com/
# 复制到项目目录
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem deploy/nginx/certs/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem deploy/nginx/certs/

# 设置权限
sudo chown root:root deploy/nginx/certs/*.pem
sudo chmod 644 deploy/nginx/certs/fullchain.pem
sudo chmod 600 deploy/nginx/certs/privkey.pem
```

**自动续期**：Certbot 会自动设置定时任务，证书到期前自动续期。

### 方式二：使用阿里云 SSL 证书

1. 登录阿里云控制台
2. 进入"SSL 证书（应用安全）"服务
3. 购买或申请免费证书
4. 下载 Nginx 格式证书
5. 解压后将证书文件放入 `deploy/nginx/certs/` 目录：
   - `your-domain.pem` → 重命名为 `fullchain.pem`
   - `your-domain.key` → 重命名为 `privkey.pem`

### 方式三：使用企业内部 CA

如果是企业内部部署，使用企业 CA 签发的证书：

```bash
# 将 CA 签发的证书链和私钥复制到 certs 目录
cp /path/to/your/certificate.pem deploy/nginx/certs/fullchain.pem
cp /path/to/your/private.key deploy/nginx/certs/privkey.pem

# 设置权限
chmod 644 deploy/nginx/certs/fullchain.pem
chmod 600 deploy/nginx/certs/privkey.pem
```

### 方式四：自签名证书（仅用于开发/测试）

**警告**：自签名证书不适用于生产环境，浏览器会显示安全警告。

```bash
# 生成自签名证书（有效期 365 天）
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout deploy/nginx/certs/privkey.pem \
  -out deploy/nginx/certs/fullchain.pem \
  -subj "/C=CN/ST=Beijing/L=Beijing/O=YourCompany/CN=your-domain.com"

# 设置权限
chmod 644 deploy/nginx/certs/fullchain.pem
chmod 600 deploy/nginx/certs/privkey.pem
```

## 验证证书

```bash
# 检查证书信息
openssl x509 -in deploy/nginx/certs/fullchain.pem -text -noout

# 检查证书和私钥是否匹配
openssl x509 -noout -modulus -in deploy/nginx/certs/fullchain.pem | openssl md5
openssl rsa -noout -modulus -in deploy/nginx/certs/privkey.pem | openssl md5
# 两个 MD5 值应该相同
```

## 部署步骤

1. **获取证书**：按照上述方式之一获取证书文件
2. **放置证书**：将证书文件放入 `deploy/nginx/certs/` 目录
3. **设置权限**：确保证书文件权限正确（私钥 600，证书 644）
4. **启动服务**：
   ```bash
   docker compose --profile full up -d
   ```
5. **验证 HTTPS**：
   ```bash
   curl -I https://your-domain.com
   ```

## 常见问题

### Q: 证书续期后如何更新？

**Let's Encrypt**：
```bash
sudo certbot renew
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem deploy/nginx/certs/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem deploy/nginx/certs/
docker compose restart nginx
```

**阿里云证书**：
1. 下载新证书
2. 替换 `deploy/nginx/certs/` 中的文件
3. 重启 Nginx：`docker compose restart nginx`

### Q: 如何使用 HTTP（开发环境）？

修改 `docker-compose.yml`，注释掉 Nginx 服务或修改 `nginx.conf`：

```nginx
# 注释掉 HTTPS 重定向
# return 301 https://$host$request_uri;

# 直接代理到后端
location / {
    proxy_pass http://lnn_api_backend;
}
```

### Q: 证书文件格式错误？

确保证书文件是 PEM 格式（Base64 编码），文件内容类似：

```
<PEM 示例内容已省略>
```

私钥文件格式：

```
<PEM 示例内容已省略>
```

## 安全建议

1. **不要将私钥提交到 Git 仓库**（已在 `.gitignore` 中排除）
2. **定期更新证书**（Let's Encrypt 90 天，建议自动续期）
3. **使用强加密算法**（已在 `nginx.conf` 中配置 TLS 1.2/1.3）
4. **启用 HSTS**（已在 `nginx.conf` 中配置）
5. **定期扫描 SSL 配置**（使用 https://www.ssllabs.com/ssltest/）

## 参考链接

- [Let's Encrypt 文档](https://letsencrypt.org/docs/)
- [Nginx SSL 配置](https://nginx.org/en/docs/http/configuring_https_servers.html)
- [阿里云 SSL 证书](https://help.aliyun.com/product/28533.html)
