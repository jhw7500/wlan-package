import os
import glob

PCAP_DIR = "/var/log/cantops/capture/tmp/"
MAX_FILES = 20

def cleanup_pcaps():
    # .pcap 파일 전체 목록을 수정시간 순으로 정렬
    pcap_files = sorted(
        glob.glob(os.path.join(PCAP_DIR, "*.pcap")),
        key=os.path.getmtime
    )

    # 파일이 MAX_FILES 개수를 초과하면 오래된 순서부터 삭제
    excess = len(pcap_files) - MAX_FILES
    if excess > 0:
        for f in pcap_files[:excess]:
            print(f"Removing old file: {f}")
            os.remove(f)

if __name__ == "__main__":
    cleanup_pcaps()
