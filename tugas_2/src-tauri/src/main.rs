#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Builder;

mod service;

use service::{
    send_request_response,
    mqtt_publish,
};

fn main() {
    Builder::default()
        .invoke_handler(tauri::generate_handler![
            send_request_response,
            mqtt_publish,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}