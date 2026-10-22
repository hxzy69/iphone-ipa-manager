$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$data = $env:IPA_TOAST | ConvertFrom-Json
$title = [Security.SecurityElement]::Escape($data.title)
$message = [Security.SecurityElement]::Escape($data.message)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$message</text></binding></visual></toast>")
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($data.app)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notifier.Show($toast)
@{ submitted = $true; setting = $notifier.Setting.ToString() } | ConvertTo-Json -Compress
