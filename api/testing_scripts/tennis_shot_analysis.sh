curl -X POST http://localhost:5001/process-tennis-shot-analysis \
  -F "shot_type=serve" \
  -F "comparison_pro=Alcaraz" \
  -F "video=@test_videos/test_serve.mp4" \
  -o response.json \
  -D headers.txt \
  -v