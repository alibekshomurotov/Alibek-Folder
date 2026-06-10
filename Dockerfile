    FROM ghcr.io/imputnet/cobalt:latest

    ENV API_URL=https://alibek00001-cobalt-api.hf.space
    ENV API_PORT=7860


    RUN cat > /init.sh << 'ENDSCRIPT'

    if [ -n "$YOUTUBE_COOKIES" ]; then
        mkdir -p /data
        printf '%s' "$YOUTUBE_COOKIES" > /data/cookies.txt
        export COOKIE_PATH=/data/cookies.txt
        echo "[INIT] YouTube cookies loaded!"
    else
        echo "[INIT] No YouTube cookies provided - YouTube may not work"
    fi
    exec "$@"
    ENDSCRIPT
    RUN chmod +x /init.sh

    EXPOSE 7860
    ENTRYPOINT ["/init.sh"]
