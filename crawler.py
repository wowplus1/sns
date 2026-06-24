import os
import sys
import json
import time
import random
import threading
import base64
import requests
import html
from datetime import datetime
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, HTTPServer

import config
import scraper

DATA_FILE = Path(__file__).resolve().parent / "data.json"

def load_existing_data():
    """기존 data.json 파일 데이터를 로드합니다."""
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Crawler] data.json 로드 중 에러 발생 (빈 배열 반환): {str(e)}")
        return []

def push_to_github():
    """GitHub REST API를 사용하여 data.json 파일을 원격 저장소에 직접 업로드합니다.
    사용자의 컴퓨터에 git 프로그램이 설치되어 있지 않아도 100% 안전하게 업로드됩니다."""
    if not config.GITHUB_TOKEN or not config.GITHUB_REPO:
        print("[Git/API] 깃허브 토큰(GITHUB_TOKEN) 또는 저장소명(GITHUB_REPO)이 .env에 설정되지 않아 자동 업로드를 건너뜁니다.")
        return False
        
    if config.GITHUB_TOKEN == "your_github_token_here" or config.GITHUB_REPO == "your_github_username/your_repo_name":
        print("[Git/API] 깃허브 설정이 초기 상태입니다. 이사님 공유를 위해 .env 파일을 설정해 주세요.")
        return False
        
    print("[Git/API] 깃허브 API를 사용해 data.json 자동 업로드를 시도합니다...")
    
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/data.json"
    headers = {
        "Authorization": f"token {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # 1. 로컬 data.json 데이터 base64 인코딩
    try:
        if not DATA_FILE.exists():
            print("[Git/API] data.json 파일이 존재하지 않아 업로드할 수 없습니다.")
            return False
            
        with open(DATA_FILE, "rb") as f:
            content_bytes = f.read()
        content_b64 = base64.b64encode(content_bytes).decode("utf-8")
    except Exception as read_err:
        print(f"[Git/API] 파일 읽기 에러: {str(read_err)}")
        return False
        
    # 2. 기존 파일의 SHA 값 조회를 위해 GET 요청
    sha = None
    try:
        get_res = requests.get(url, headers=headers, timeout=10)
        if get_res.status_code == 200:
            sha = get_res.json().get("sha")
    except Exception as get_err:
        print(f"[Git/API] 기존 파일 정보(SHA) 가져오기 중 에러 발생 (새로 생성 시 무관): {str(get_err)}")
        
    # 3. 깃허브 저장소에 덮어쓰기 (PUT 요청)
    payload = {
        "message": f"auto: update threads posts {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha
        
    try:
        put_res = requests.put(url, headers=headers, json=payload, timeout=15)
        if put_res.status_code in (200, 201):
            print("[Git/API] 깃허브 업로드 성공!")
            return True
        else:
            print(f"[Git/API] 깃허브 업로드 실패. 상태 코드: {put_res.status_code}, 응답: {put_res.text}")
            return False
    except Exception as put_err:
        print(f"[Git/API] 깃허브 API 호출(PUT) 중 예외 발생: {str(put_err)}")
        return False

def save_data(data):
    """데이터를 data.json에 저장합니다 (최대 2000건 보존)."""
    # 시간 순 정렬 후 최근 2000건만 유지하여 파일 크기 최적화
    data = sorted(data, key=lambda x: x.get("created_at", ""), reverse=True)
    trimmed_data = data[:2000]
    
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(trimmed_data, f, ensure_ascii=False, indent=2)
        print(f"[Crawler] 데이터 저장 완료 (총 {len(trimmed_data)}건 보존).")
        
        # 백그라운드 스레드로 깃 업로드 실행 (수집 루틴 지연 방지)
        # daemon=False로 기동하여, 단독 실행 시에도 업로드가 끝날 때까지 프로세스가 종료 대기하도록 조치
        threading.Thread(target=push_to_github, daemon=False).start()
        return True
    except Exception as e:
        print(f"[Crawler] 데이터 저장 실패: {str(e)}")
        return False

def run_crawl_once(platform="threads"):
    print(f"\n==================================================")
    print(f" [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 모니터링 수집 태스크 기동 (대상 플랫폼: {platform})")
    print(f"==================================================")
    
    # 1. 기존 데이터 로드 및 중복 체크용 포스트 ID 집합 생성
    existing_posts = load_existing_data()
    existing_ids = {post["post_id"] for post in existing_posts}
    
    new_posts_to_add = []
    
    # 2. 키워드별 수집 시작
    for keyword in config.KEYWORDS:
        print(f"[Crawler] 키워드 '{keyword}' 수집 중...")
        try:
            # 지정된 플랫폼 수집
            scraped_results = scraper.run_scraper(keyword, platform=platform)
            
            new_kw_count = 0
            for item in scraped_results:
                post_id = item["post_id"]
                
                # 중복 게시물 패스
                if post_id in existing_ids:
                    continue
                
                # 신규 글일 시 타임스탬프 추가 (기존에 파싱된 시간이 없을 때만 수집일시 기입)
                if "created_at" not in item or not item["created_at"]:
                    item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # 신규 등록
                new_posts_to_add.append(item)
                existing_ids.add(post_id)
                new_kw_count += 1
                
            print(f"[Crawler] '{keyword}' 결과: 신규 글 {new_kw_count}건 발견.")
            
        except Exception as e:
            print(f"[Crawler] '{keyword}' 수집 중 에러 발생: {str(e)}")
            
        # 차단 방지를 위한 랜덤 딜레이 (여러 키워드를 돌 때)
        if len(config.KEYWORDS) > 1:
            delay = random.randint(15, 20) if platform == "instagram" else random.randint(4, 7)
            print(f"[Crawler] 안전 대기 시간 {delay}초를 부여합니다...")
            time.sleep(delay)

    # 3. 새로운 글이 존재한다면 DB(JSON)에 병합 및 저장
    if new_posts_to_add:
        updated_data = existing_posts + new_posts_to_add
        save_data(updated_data)
        print(f"[Crawler] 수집 완료. 신규 {len(new_posts_to_add)}개 글 갱신 적재 성공.")
    else:
        print("[Crawler] 수집 완료. 새로 발견된 게시물이 없습니다.")

def main():
    # 명령줄 파라미터로 플랫폼 수집 처리 가능케 함
    target_platform = "threads"
    if len(sys.argv) > 1:
        if sys.argv[1] == "--instagram":
            target_platform = "instagram"
        elif sys.argv[1] == "--naver":
            target_platform = "naver"
        elif sys.argv[1] == "--youtube":
            target_platform = "youtube"
        elif sys.argv[1] in ("--twitter", "--x"):
            target_platform = "twitter"
        elif sys.argv[1] in ("--naver-cafe", "--cafe"):
            target_platform = "naver_cafe"
    run_crawl_once(platform=target_platform)

class APIServerHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # CORS 허용 및 캐시 방지 헤더 설정
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/get_gemini_key":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            response_body = json.dumps({
                "key": config.GEMINI_API_KEY
            }, ensure_ascii=False)
            self.wfile.write(response_body.encode('utf-8'))
        else:
            super().do_GET()

    def do_POST(self):
        if self.path in ("/api/crawl", "/api/crawl/instagram", "/api/crawl/naver", "/api/crawl/youtube", "/api/crawl/twitter", "/api/crawl/naver_cafe"):
            if self.path == "/api/crawl/instagram":
                target_platform = "instagram"
            elif self.path == "/api/crawl/naver":
                target_platform = "naver"
            elif self.path == "/api/crawl/youtube":
                target_platform = "youtube"
            elif self.path == "/api/crawl/twitter":
                target_platform = "twitter"
            elif self.path == "/api/crawl/naver_cafe":
                target_platform = "naver_cafe"
            else:
                target_platform = "threads"
                
            print(f"[Server] 실시간 수집({target_platform}) 요청 수신.")
            try:
                # 1. 크롤링 즉시 구동 (동기 실행으로 최신글 갱신 보장)
                run_crawl_once(platform=target_platform)
                
                # 2. 최신 data.json 다시 읽기
                latest_data = load_existing_data()
                
                # 3. 결과 반환
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                
                if target_platform == "naver":
                    platform_ko = "네이버 블로그"
                elif target_platform == "youtube":
                    platform_ko = "유튜브"
                elif target_platform == "instagram":
                    platform_ko = "인스타그램"
                elif target_platform == "twitter":
                    platform_ko = "트위터"
                elif target_platform == "naver_cafe":
                    platform_ko = "네이버 카페"
                else:
                    platform_ko = "쓰레드"
                    
                response_body = json.dumps({
                    "status": "success",
                    "message": f"{platform_ko} 실시간 수집이 성공적으로 완료되었습니다.",
                    "data": latest_data
                }, ensure_ascii=False)
                self.wfile.write(response_body.encode('utf-8'))
            except Exception as e:
                print(f"[Server] 실시간 수집 중 에러 발생: {str(e)}")
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                
                response_body = json.dumps({
                    "status": "error",
                    "message": f"실시간 수집 실패: {str(e)}"
                }, ensure_ascii=False)
                self.wfile.write(response_body.encode('utf-8'))
        elif self.path == "/api/generate_reply":
            print("[Server] AI 추천 댓글 생성 요청 수신.")
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                req_json = json.loads(post_data.decode('utf-8'))
                post_content = req_json.get("content", "")
                
                api_key = config.GEMINI_API_KEY
                if not api_key:
                    raise Exception("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 연동해 주세요.")
                
                # REST API 방식으로 Gemini 모델 호출
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent?key={api_key}"
                headers = {"Content-Type": "application/json"}
                
                # 프롬프트 설정 (JSON 배열 형태로 답변하도록 강제)
                prompt = (
                    "너는 제주도 감성 숙소 '비로소433'의 사장님이야. "
                    "다음 제주 여행객이 작성한 SNS 본문을 친절하게 읽고, 우리 숙소를 부담스럽지 않고 센스 있게 추천하거나 환영하는 댓글 초안(120자 이내, 구어체, 친근한 이모지 1~2개 포함)을 서로 다른 다양한 톤앤매너와 내용으로 4개 생성해줘. "
                    "출력 포맷은 반드시 다음과 같은 JSON 배열 형식으로만 출력해줘. 다른 설명이나 텍스트는 절대 포함하지 마:\n"
                    "[\"첫 번째 댓글 초안\", \"두 번째 댓글 초안\", \"세 번째 댓글 초안\", \"네 번째 댓글 초안\"]\n\n"
                    f"[여행객 게시글 본문]: {post_content}"
                )
                
                payload = {
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }]
                }
                
                res = requests.post(url, headers=headers, json=payload, timeout=15)
                if res.status_code == 200:
                    res_data = res.json()
                    try:
                        text = res_data['candidates'][0]['content']['parts'][0]['text']
                        # JSON 패턴 추출
                        import re
                        json_match = re.search(r'\[.*\]', text, re.DOTALL)
                        if json_match:
                            replies = json.loads(json_match.group(0))
                        else:
                            replies = [text.strip()]
                    except Exception as parse_err:
                        print(f"[Gemini] 응답 파싱 에러: {str(parse_err)}")
                        replies = [
                            "감사합니다! 제주여행 중이시군요. 저희 비로소433에서 편안한 휴식을 누려보세요. 🏡✨",
                            "제주에서 힐링이 필요할 땐 언제든 비로소433을 찾아주세요! 행복한 여행 되세요. 🌴😊",
                            "제주 푸른 바다와 함께 비로소433에서 여유로운 시간을 가져보시는 건 어떨까요? 🌊🏡",
                            "비로소433에서 제주 여행의 특별한 추억을 더해보세요. 언제든 환영합니다! 🍊✨"
                        ]
                else:
                    raise Exception(f"API 응답 실패 (상태 코드: {res.status_code}): {res.text}")
                
                # 결과 반환
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                
                response_body = json.dumps({
                    "status": "success",
                    "replies": replies
                }, ensure_ascii=False)
                self.wfile.write(response_body.encode('utf-8'))
            except Exception as e:
                print(f"[Server] AI 댓글 생성 실패: {str(e)}")
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.end_headers()
                
                response_body = json.dumps({
                    "status": "error",
                    "message": f"AI 댓글 생성 중 에러: {str(e)}"
                }, ensure_ascii=False)
                self.wfile.write(response_body.encode('utf-8'))
        else:
            super().do_POST()

def is_night_time():
    """현재 시각이 야간 시간대(00:00 ~ 07:00)인지 여부를 반환합니다."""
    current_hour = datetime.now().hour
    return 0 <= current_hour < 7

def run_integrated_crawl():
    """모든 플랫폼(Threads, Naver, Youtube, Instagram, Twitter, Cafe)을 
    순차적으로 수집하여 최종 1회만 데이터 저장 및 깃허브 업로드를 수행합니다."""
    print(f"\n==================================================")
    print(f" [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 통합 모니터링 수집 태스크 기동")
    print(f"==================================================")
    
    # 1. 기존 데이터 로드
    existing_posts = load_existing_data()
    existing_ids = {post["post_id"] for post in existing_posts}
    
    new_posts_to_add = []
    
    # 수집할 플랫폼 리스트 구성
    # 기본 플랫폼
    platforms = ["threads", "naver", "youtube"]
    
    # 야간 시간대(00:00 ~ 07:00)가 아니면 나머지 플랫폼도 수집
    if not is_night_time():
        platforms.extend(["instagram", "twitter", "naver_cafe"])
        
    print(f"[Integrated Crawler] 수집 대상 플랫폼: {platforms}")
    
    # 각 플랫폼 순회하며 수집
    for platform in platforms:
        print(f"[Integrated Crawler] 플랫폼 '{platform}' 수집 중...")
        for keyword in config.KEYWORDS:
            try:
                # scraper.run_scraper를 이용해 수집
                scraped_results = scraper.run_scraper(keyword, platform=platform)
                
                new_kw_count = 0
                for item in scraped_results:
                    post_id = item["post_id"]
                    
                    if post_id in existing_ids:
                        continue
                        
                    if "created_at" not in item or not item["created_at"]:
                        item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                    new_posts_to_add.append(item)
                    existing_ids.add(post_id)
                    new_kw_count += 1
                
                print(f"[Integrated Crawler] '{keyword}' ({platform}) 결과: 신규 {new_kw_count}건 발견.")
            except Exception as e:
                print(f"[Integrated Crawler] '{keyword}' ({platform}) 수집 중 에러 발생: {str(e)}")
            
            # 플랫폼/키워드 간 안전 딜레이
            delay = random.randint(5, 8)
            print(f"[Integrated Crawler] 안전 대기 시간 {delay}초 부여...")
            time.sleep(delay)
            
    # 모든 플랫폼의 수집이 완료된 시점에 딱 1번만 일괄 저장 및 깃허브 푸시
    if new_posts_to_add:
        updated_data = existing_posts + new_posts_to_add
        save_data(updated_data)
        print(f"[Integrated Crawler] 통합 수집 완료 및 업로드 시작. 신규 {len(new_posts_to_add)}개 글 갱신 적재 성공.")
    else:
        print("[Integrated Crawler] 통합 수집 완료. 새로 발견된 게시물이 없습니다.")

def start_integrated_scheduler():
    """설정된 주기마다 백그라운드에서 전체 플랫폼 통합 크롤링을 자동 수행하는 스케줄러"""
    print(f"[Scheduler] 통합 자동 수집 스케줄러 시작 (주기: {config.MONITOR_INTERVAL}분)")
    
    # 서버 기동 30초 대기 후 최초 1회 통합 수집 실행 (서버 초기 부하 방지)
    time.sleep(30)
    try:
        print("[Scheduler] 서버 시작에 따른 최초 통합 즉시 수집 시작")
        run_integrated_crawl()
    except Exception as init_err:
        print(f"[Scheduler] 최초 통합 수집 중 에러 발생: {str(init_err)}")
        
    while True:
        # 설정된 주기를 초 단위로 계산하여 대기
        interval_seconds = config.MONITOR_INTERVAL * 60
        print(f"[Scheduler] 다음 통합 수집 대기 시간: {config.MONITOR_INTERVAL}분")
        time.sleep(interval_seconds)
        try:
            print(f"[Scheduler] {config.MONITOR_INTERVAL}분 주기 통합 자동 수집 시작")
            run_integrated_crawl()
        except Exception as scheduler_err:
            print(f"[Scheduler] 통합 자동 수집 에러 발생: {str(scheduler_err)}")

def start_server(port=8001):
    server_address = ('', port)
    directory = str(Path(__file__).resolve().parent)
    
    class LocalFilesHandler(APIServerHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

    httpd = HTTPServer(server_address, LocalFilesHandler)
    
    # 통합 백그라운드 자동 수집 스케줄러 스레드 단 하나만 기동
    integrated_scheduler_thread = threading.Thread(target=start_integrated_scheduler, daemon=True)
    integrated_scheduler_thread.start()
    
    print(f"\n==================================================")
    print(f" [Server] 내장 API 및 대시보드 서버 기동 완료")
    print(f" [Server] 접속 주소: http://localhost:{port}")
    print(f"==================================================")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] 서버를 종료합니다.")
        httpd.server_close()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        start_server(8001)
    else:
        main()
