ps aux | grep -i "multiprocessing" | awk '{print $2}' | xargs kill -9
