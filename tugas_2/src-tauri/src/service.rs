use std::time::Duration;
use rumqttc::{AsyncClient, MqttOptions, QoS};
use tokio::time;
use tokio::spawn;

#[tauri::command]
pub async fn send_request_response(id: String, latency: u64) -> Result<String, String> {

    time::sleep(Duration::from_millis(latency)).await;
    Ok(format!("response for request ID: {} latency: {}", id, latency))
}

#[tauri::command]
pub async fn mqtt_publish(topic: String, message: String, latency: u64) -> Result<(), String> {
    time::sleep(Duration::from_millis(latency)).await;

    let mut mqttoptions = MqttOptions::new("tauri_publisher", "localhost", 1883);
    mqttoptions.set_keep_alive(Duration::from_secs(5));

    let (client, mut eventloop) = AsyncClient::new(mqttoptions, 10);

    spawn(async move {
        for _ in 0..10 { 
            let _ = eventloop.poll().await;
        }
    });

    client.publish(topic, QoS::AtMostOnce, false, message)
        .await
        .map_err(|e| e.to_string())?;

    Ok(())
}