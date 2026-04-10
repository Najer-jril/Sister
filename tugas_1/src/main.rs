use std::fs::{self, File};
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::sync::mpsc::sync_channel;
use std::thread;
use std::time::Instant;

const FILE_PATH: &str = "../dataset/year_2021.csv";
const NUM_THREADS: u64 = 10;
const BATCH_SIZE: usize = 10000;
const QUEUE_SIZE: usize = 500;

fn single_thread() -> (f64, usize) {
    let start = Instant::now();
    let file = File::open(FILE_PATH).expect("gada filenya");
    let reader = BufReader::new(file);
    
    let mut line_count = 0;
    for _line in reader.lines() {
        line_count += 1;
    }
    
    (start.elapsed().as_secs_f64(), line_count)
}

fn two_threads() -> (f64, usize) {
    let start = Instant::now();
    let (tx, rx) = sync_channel::<Vec<String>>(QUEUE_SIZE);

    let producer = thread::spawn(move || {
        let file = File::open(FILE_PATH).unwrap();
        let reader = BufReader::new(file);
        let mut batch = Vec::with_capacity(BATCH_SIZE);

        for line in reader.lines() {
            if let Ok(l) = line {
                batch.push(l);
                if batch.len() >= BATCH_SIZE {
                    tx.send(batch).unwrap();
                    batch = Vec::with_capacity(BATCH_SIZE);
                }
            }
        }
        if !batch.is_empty() {
            tx.send(batch).unwrap();
        }
    });

    let consumer = thread::spawn(move || {
        let mut count = 0;
        while let Ok(batch) = rx.recv() {
            count += batch.len();
        }
        count
    });

    producer.join().unwrap();
    let total_lines = consumer.join().unwrap();

    (start.elapsed().as_secs_f64(), total_lines)
}

fn multi_threads() -> (f64, usize) {
    let start = Instant::now();
    let file_size = fs::metadata(FILE_PATH).unwrap().len();
    let chunk_size = file_size / NUM_THREADS;
    
    let (tx, rx) = sync_channel::<Vec<String>>(QUEUE_SIZE);

    let consumer = thread::spawn(move || {
        let mut count = 0;
        while let Ok(batch) = rx.recv() {
            count += batch.len();
        }
        count
    });

    let mut producers = vec![];
    for i in 0..NUM_THREADS {
        let tx_clone = tx.clone(); 
        let start_byte = i * chunk_size;
        let end_byte = if i == NUM_THREADS - 1 { file_size } else { (i + 1) * chunk_size };

        let t = thread::spawn(move || {
            let mut file = File::open(FILE_PATH).unwrap();
            file.seek(SeekFrom::Start(start_byte)).unwrap();
            
            let mut current_pos = start_byte;
            let mut reader = BufReader::new(file);

            if start_byte != 0 {
                let mut discard = String::new();
                let bytes_read = reader.read_line(&mut discard).unwrap() as u64;
                current_pos += bytes_read;
            }

            let mut batch = Vec::with_capacity(BATCH_SIZE);
            while current_pos < end_byte {
                let mut line = String::new();
                let bytes_read = reader.read_line(&mut line).unwrap();
                if bytes_read == 0 { break; }
                
                current_pos += bytes_read as u64;
                batch.push(line);

                if batch.len() >= BATCH_SIZE {
                    tx_clone.send(batch).unwrap();
                    batch = Vec::with_capacity(BATCH_SIZE);
                }
            }
            if !batch.is_empty() {
                tx_clone.send(batch).unwrap();
            }
        });
        producers.push(t);
    }

    drop(tx);

    for t in producers {
        t.join().unwrap();
    }
    let total_lines = consumer.join().unwrap();

    (start.elapsed().as_secs_f64(), total_lines)
}

fn main() {
    let (d1, _c1) = single_thread();
    let (d2, _c2) = two_threads();
    let (d3, _c3) = multi_threads();

    println!("single, {:.4} s", d1);
    println!("1 read 1 process, {:.4} s", d2);
    println!("multi threads, {:.4} s", d3);
}