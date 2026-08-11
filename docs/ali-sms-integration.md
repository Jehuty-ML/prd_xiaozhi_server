# 阿里云短信（智控台手机注册）

短信登录/注册由 **`manager-api`** 实现（`ALiYunSmsService`），与六微服务语音链路无关。需全模块部署并打开智控台参数。

登录阿里云控制台，进入「短信服务」：https://dysms.console.aliyun.com/overview

## 第一步 添加签名

![步骤](images/alisms/sms-01.png)
![步骤](images/alisms/sms-02.png)

将签名写入智控台参数：`aliyun.sms.sign_name`

## 第二步 添加模板

![步骤](images/alisms/sms-11.png)

将模板 code 写入：`aliyun.sms.sms_code_template_code`

签名报备可能需数个工作日，报备成功后再测发送。

## 第三步 创建短信账户和开通权限

「访问控制」：https://ram.console.aliyun.com/overview?activeTab=overview

![步骤](images/alisms/sms-21.png)
![步骤](images/alisms/sms-22.png)
![步骤](images/alisms/sms-23.png)
![步骤](images/alisms/sms-24.png)
![步骤](images/alisms/sms-25.png)

写入：`aliyun.sms.access_key_id`、`aliyun.sms.access_key_secret`

## 第四步 启动手机注册

1. 参数填齐后效果大致如下：

![步骤](images/alisms/sms-31.png)

2. `server.allow_user_register` = `true`
3. `server.enable_mobile_register` = `true`

![步骤](images/alisms/sms-32.png)
