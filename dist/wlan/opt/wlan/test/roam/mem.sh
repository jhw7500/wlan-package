# 각 PID의 RSS 확인
for p in 32510 32530 32531; do
  printf "%s " $p; ps -o pid,comm,rss,vsz -p $p | tail -1
done

# cgroup 합산이 아닌 개별 프로세스의 전체 사용량(approx)
pmap -x 32510 | tail -1
pmap -x 32530 | tail -1
pmap -x 32531 | tail -1

# 파일디스크립터 누수 의심 점검
for p in 32510 32530 32531; do
  printf "PID %s FDs: " $p; ls /proc/$p/fd | wc -l
done
