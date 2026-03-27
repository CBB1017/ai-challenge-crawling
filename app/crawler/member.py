import os
import re

from loguru import logger
from bs4 import BeautifulSoup
import asyncio

from app.crawler.base import BaseCrawler

class MemberCrawler(BaseCrawler):
    def __init__(self, login_url: str, username: str, password: str, cookies: list = None):
        super().__init__(login_url, username, password, cookies)

    async def fetch_members(self, groupware_domain: str):
        # 1. 쿠키 확인 및 자동 로그인
        if not self.cookies:
            success, new_cookies = await self.login()
            if not success:
                logger.error("[STEP1] 로그인 실패")
                return {"status": "fail", "message": "로그인 실패", "data": None}
            self.cookies = new_cookies

        try:
            logger.info("[STEP2] 조직도/이메일 동시 크롤링 시작")
            # 2. 동시 크롤링 (gather)
            tree_js, email_options_html = await asyncio.gather(
                self.get_tree_js_data(groupware_domain),
                self.get_email_options_html(groupware_domain)
            )

            if not tree_js:
                return {"status": "fail", "message": "조직도 데이터 없음", "data": None}
            if not email_options_html:
                return {"status": "fail", "message": "이메일 데이터 없음", "data": None}

            # 3. 데이터 파싱 및 병합
            logger.info("[STEP3~6] 파싱 및 병합 시작")
            node_list = self.parse_d_add_lines(tree_js)
            email_map, name_count = self.get_email_map(email_options_html)
            teams = self.extract_teams_and_members(node_list)
            merged_teams = self.merge_member_email(teams, email_map, name_count)

            # 4. 후처리
            logger.info("[STEP7] None -> 빈 문자열 처리")
            result = self.none_to_empty(merged_teams)

            return {
                "status": "success",
                "message": "조직도 조회 성공",
                "data": result,
                "cookies": self.cookies
            }

        except Exception as e:
            logger.exception(f"크롤링 중 예외 발생: {e}")
            return {"status": "fail", "message": str(e), "data": None}

    async def get_email_options_html(self, groupware_domain: str):
        email_url = f"{groupware_domain}/includes/Comm_PopSearchResult?tmpD=&tmpA=A&tmpC=0&tmpB=17&tmpE=AreaAll&tmpf=%EC%A0%84%EC%B2%B4&tmpG=&tmpH=email"

        # 💡 개선: 공용 browser context 대신, 쿠키가 주입된 독립 context 사용
        page = await self.context.new_page()
        await page.goto(email_url)
        html = await page.content()
        await page.close()
        return html

    async def get_tree_js_data(self, groupware_domain: str):
        tree_url = f"{groupware_domain}/Tree/TreeVertical_R2"
        page = await self.context.new_page()
        await page.goto(tree_url)
        content = await page.content()
        await page.close()

        soup = BeautifulSoup(content, "html.parser")
        for script in soup.find_all("script"):
            js_code = script.get_text()
            if js_code and "d.add(" in js_code:
                return js_code
        return None

    @staticmethod
    def parse_js_params(line):
        fields = []
        cur = ''
        in_squote = False
        in_dquote = False
        prev = ''
        for c in line:
            if in_squote:
                if c == "'" and prev != '\\':
                    in_squote = False
                    fields.append(cur)
                    cur = ''
                else:
                    cur += c
            elif in_dquote:
                if c == '"' and prev != '\\':
                    in_dquote = False
                    fields.append(cur)
                    cur = ''
                else:
                    cur += c
            else:
                if c == "'":
                    in_squote = True
                elif c == '"':
                    in_dquote = True
                elif c == ',':
                    if cur.strip():
                        fields.append(cur.strip())
                    cur = ''
                else:
                    cur += c
            prev = c
        if cur.strip():
            fields.append(cur.strip())
        return fields

    @staticmethod
    def parse_d_add_lines(js_text):
        pattern = re.compile(r"d\.add\((.+?)\);")
        nodes = []
        for match in pattern.finditer(js_text):
            line = match.group(1)
            try:
                fields = MemberCrawler.parse_js_params(line)
                # 디버깅 로그
                logger.debug("f[PARSE] fields: %s", fields)
                # 필드 개수 맞추기
                while len(fields) < 8:
                    fields.append('')
                node = {
                    "id": fields[0],
                    "parent_id": fields[1],
                    "label": fields[2],
                    "onclick": fields[3],
                }
                nodes.append(node)
            except Exception as e:
                logger.error("파싱오류: %s\n에러: %s", line, e)
        return nodes

    @staticmethod
    def get_email_map(options_html):
        soup = BeautifulSoup(options_html, "html.parser")
        email_map = {}
        name_count = {}
        for opt in soup.find_all("option"):
            v = opt.get("value")
            txt = opt.text.strip() if opt.text else ""
            if not v or "|" not in v or "<" not in txt:
                continue
            account, dept = v.split("|", 1)
            m = re.match(r"(.+?)<([^>]+)>", txt)
            if not m:
                continue
            name, email = m.group(1).strip(), m.group(2).strip()
            key = (account, dept)
            if key in email_map:
                name_count[key] = name_count.get(key, 1) + 1
            else:
                email_map[key] = (name, email)
                name_count[key] = 1
        return email_map, name_count

    @staticmethod
    def extract_teams_and_members(node_list):
        node_dict = {n['id']: {**n, "children": []} for n in node_list}
        roots = []
        for node in node_dict.values():
            pid = node['parent_id']
            if pid in node_dict:
                node_dict[pid]['children'].append(node)
            else:
                roots.append(node)

        def is_team_node(node):
            # 자식 중 fShowPerson이 하나라도 있으면 팀 폴더
            return any("fShowPerson" in c.get("onclick", "") for c in node.get("children", []))

        def parse_member(child):
            label = child.get("label", "")
            tm = re.match(r"(.+?)(?:\(([\d:]+)\))?(?:\s*\[외부\])?$", label)
            base = tm.group(1).strip() if tm else label
            external = "[외부]" in label
            parts = base.split()
            if len(parts) >= 2:
                name, position = parts[0], parts[1]
            else:
                name, position = base, ""
            attend_time = tm.group(2) if tm and tm.group(2) else ""

            onclick = child.get("onclick", "")
            account = ""
            deptCode = ""
            m = re.match(r"javascript:fShowPerson\('([^']+)','([^']+)'", onclick)
            if m:
                account = m.group(1)
                deptCode = m.group(2)
            return {
                "memberName": name,
                "position": position,
                "time": attend_time,
                "external": external,
                "account": account,
                "deptCode": deptCode,
                "email": "",
            }

        teams = []

        def traverse(node):
            # 팀 폴더(부서)면
            if is_team_node(node):
                team_name = node.get("label", "")
                # 직속 자식 중 fShowPerson 멤버만 추출
                members = [parse_member(c) for c in node.get("children", []) if "fShowPerson" in c.get("onclick", "")]
                if members:
                    teams.append({"team": team_name, "members": members})
            # 모든 자식도 재귀 탐색(폴더 안에 폴더 구조)
            for c in node.get("children", []):
                traverse(c)

        for r in roots:
            traverse(r)

        return teams

    @staticmethod
    def merge_member_email(teams, email_map, name_count):
        for team in teams:
            for m in team["members"]:
                key = (m["account"], m["deptCode"])
                logger.debug(
                    f"[EMAIL-MATCH-DEBUG] member: {m['memberName']} key={key} name_count={name_count.get(key, 0)} email={email_map.get(key, (None, '없음'))[1]}")
                if name_count.get(key, 0) > 1:
                    m["email"] = ""
                    logger.error(
                        f"동명이인(부서:{m['deptCode']}, 계정:{m['account']}) - 이메일 불확정: {m['memberName']} {m['position']}")
                else:
                    m["email"] = email_map.get(key, (None, ""))[1] or ""
        return teams

    def none_to_empty(self, obj):
        if isinstance(obj, dict):
            return {k: self.none_to_empty(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.none_to_empty(i) for i in obj]
        else:
            return "" if obj is None else obj
