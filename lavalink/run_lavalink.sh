#!/bin/bash

function run_lavalink () {
    echo "Starting Lavalink..."
    java -Djdk.tls.client.protocols=TLSv1.1,TLSv1.2 -jar Lavalink.jar
}

function download_lavalink() {
    echo "Lavalink.jar does not exist, downloading it..."
    wget -nv https://github.com/freyacodes/Lavalink/releases/download/3.3.2.5/Lavalink.jar
}

{
    run_lavalink
} || {
    rm -f ./Lavalink.jar # Just in case the file is corrupt
    download_lavalink
    run_lavalink
}