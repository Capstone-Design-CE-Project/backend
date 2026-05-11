import os
from konlpy.tag import Kkma
from konlpy.tag import Okt
import re
import warnings
import logging
import time

# 경고 무시
warnings.filterwarnings('ignore')
logging.getLogger('jpype').setLevel(logging.ERROR)

# 디버그 플래그
DEBUG_MORPHS = True  # 형태소 분석 결과 출력

kkma = Kkma()
okt = Okt()
nouns = [] # 나왔던 명사 저장
positions = ["왼쪽", "오른쪽", "위", "아래", "앞", "뒤","전","후","안","밖","속","겉","옆","근처","곁","사이","너머","건너","그쪽"] # 상대적 위치 저장
predicate_nouns = ["모양", "색", "맛", "냄새", "크기", "형태", "상태", "모습", "용모"] # 술어 명사: 주어의 속성을 나타냄
last_subject = None  # 가장 최근에 나왔던 주어 저장

# [Level 3] 회상 치료 특화 위험어 사전 (추가됨)
DANGER_WORDS = ["죽", "피", "때리", "부러지", "자살", "사고", "다치", "훔치"]

def is_safe_tokens(tokens):
    """[Level 3] 위험어 필터링: 토큰 리스트에서 위험한 단어가 정확히 포함되어 있는지 확인"""
    for word, pos in tokens:
        # 동사/형용사의 경우 기본형으로 변환
        if pos in ['VV', 'VXV']:
            base_form = get_base_form(word, pos)
        else:
            base_form = word
        
        # 위험단어와 정확히 일치하는지 확인
        if base_form in DANGER_WORDS:
            if DEBUG_MORPHS:
                print(f"[DEBUG] 위험어 감지: '{base_form}' (원형: '{word}', 품사: {pos})")
            return False
    
    return True

def mask_pii(text):
    """[Level 3] 개인정보(PII) 비식별화: 전화번호, 주민번호 등 마스킹"""
    # 휴대폰 번호 마스킹 (예: 010-1234-5678 -> 010-****-****)
    text = re.sub(r'01[016789]-?\d{3,4}-?\d{4}', '010-****-****', text)
    # 주민등록번호 마스킹
    text = re.sub(r'\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-?[1-4]\d{6}', '******-*******', text)
    return text

def detect_negation(tokens, verb_index):
    """동사 앞의 부정 표현 감지 (안, 못, 아니)"""
    negations = ["안", "못", "아니"]
    
    # 동사 직전 토큰들 확인 (최대 2개 토큰 앞까지)
    for i in range(max(0, verb_index - 2), verb_index):
        word, pos = tokens[i]
        if word in negations:
            if DEBUG_MORPHS:
                print(f"[DEBUG] 부정 표현 감지: '{word}' (위치: {i}, 동사 위치: {verb_index})")
            return word
    
    return None

def get_base_form(word, pos=None):
    """[Level 1] 동사/형용사 원형 복원 (Lemmatization)"""
    # pos가 VV/VXV면 동사로 직접 처리
    if pos in ['VV', 'VXV']:
        if not word.endswith("다"):
            base = word + "다"
            if DEBUG_MORPHS:
                print(f"[DEBUG] get_base_form: KKMA pos 기반 '{word}' ({pos}) -> 기본형: '{base}'")
            return base
        return word
    
    tokens = okt.pos(word, stem=True)
    
    if DEBUG_MORPHS:
        print(f"[DEBUG] get_base_form 호출: word='{word}', pos={pos}, OKT 결과={tokens}")
    
    if tokens:
        base = tokens[0][0]
        pos_tag = tokens[0][1] if len(tokens) > 0 else None
        
        if DEBUG_MORPHS:
            print(f"[DEBUG] get_base_form: base='{base}', pos_tag='{pos_tag}'")
        
        # 동사인 경우 기본형에 '다' 붙이기
        if pos_tag == "Verb":
            if not base.endswith("다"):
                base = base + "다"
                if DEBUG_MORPHS:
                    print(f"[DEBUG] get_base_form: '{word}' (Verb) -> 기본형 생성: '{base}'")
            return base
        
        return base
    return word

# 동사 사전: 동사 원형 기준 → 필요한 역할들
# (이전에는 '먹이' 였으나, 이제 원형 복원이 되므로 '먹이다', '주다', '먹다' 로 기준을 변경하는 것이 좋습니다)
verb_patterns = {
    "먹이다": ["subject", "indirect_object", "object"],
    "주다": ["subject", "indirect_object", "object"],
    "먹다": ["subject", "object"],
    "보다": ["subject", "object"],
    "일어나다": ["subject"],
    "달리다" : ["subject"],
}

class imagine:
    """명사와 그 수식어(형용사)를 저장하는 클래스"""
    def __init__(self, noun, adjectives=None):
        self.noun = noun  # 명사
        self.adjectives = adjectives if adjectives else []  # 명사의 수식어(형용사 원형)
        self.position = [] # 위치 정보: [(대상명사, 위치), ...] 형태로 저장 (예: [("등대", "옆"), ("바닷가", "앞")])
        self.moderators = []  
        self.target = None
    
    def add_adjective(self, adjective):
        self.adjectives.append(adjective)
    
    def print_noun(self):
        print(f"명사: {self.noun}")
    
    def print_adjectives(self):
        print(f"형용사: {self.adjectives}")
    
    def print_all(self):
        print(f"명사: {self.noun}")
        print(f"형용사: {self.adjectives}")
        # 위치 정보를 보기 좋게 포맷팅
        if self.position:
            position_str = ", ".join([f"{target}_{location}" for target, location in self.position])
            print(f"위치: {position_str}")
        else:
            print(f"위치: []")
        print(f"수식하는 명사들: {self.moderators}")
    
    def __repr__(self):
        return f"<{self.noun}: {self.adjectives} {self.position} Moderators: {self.moderators}>"

class Relation:
    """[Level 2] 동사 중심 관계를 저장하는 클래스: (주어, 원형 동사, 목적어, [간접목적어], [부정])"""
    def __init__(self, subject, verb, obj, indirect_object=None, negation=None):
        self.subject = subject  # imagine 객체 (주어)
        self.verb = verb  # 동사 (원형)
        self.obj = obj  # imagine 객체 (목적어)
        self.indirect_object = indirect_object  # imagine 객체 (간접목적어, 선택사항)
        self.negation = negation  # "안", "못", "아니" 등 (선택사항)
    
    def __repr__(self):
        obj_noun = self.obj.noun if self.obj else "None"
        indirect_str = f" - {self.indirect_object.noun}에게" if self.indirect_object else ""
        negation_str = f"[{self.negation}]" if self.negation else ""
        return f"({self.subject.noun} {negation_str}{self.verb} {obj_noun}){indirect_str}"
    
    def print_relation(self):
        obj_noun = self.obj.noun if self.obj else "None"
        indirect_str = f" - {self.indirect_object.noun}에게" if self.indirect_object else ""
        negation_str = f"[{self.negation}]" if self.negation else ""
        print(f"({self.subject.noun} {negation_str}{self.verb} {obj_noun}){indirect_str}")

def map_josa_to_kkma_tag(josa: str) -> str:
    josa_tag_map = {
        "이": "JKS", "가": "JKS",
        "은": "JX", "는": "JX",
        "을": "JKO", "를": "JKO",
        "에": "JKM", "에서": "JKM", "에게": "JKM",
        "의": "JKG",
        "와": "JC", "과": "JC", "랑": "JC",
        "도": "JX", "만": "JX",
    }
    return josa_tag_map.get(josa, "JX")

def postprocess_kkma_pos(sentence: str):
    raw = kkma.pos(sentence)
    if DEBUG_MORPHS:
        print(f"\n[DEBUG] ===== 형태소 분석 시작 =====")
        print(f"[DEBUG] 입력 문장: {sentence}")
        print(f"[DEBUG] KKMA 원본 결과: {raw}")
    
    processed = []

    for word, pos in raw:
        if pos in {"NNG", "NNP"}:
            okt_tokens = okt.pos(word, norm=True, stem=True)
            if (
                len(okt_tokens) == 2
                and okt_tokens[0][1] == "Noun"
                and okt_tokens[1][1] == "Josa"
            ):
                noun = okt_tokens[0][0]
                josa = okt_tokens[1][0]
                if noun + josa == word:
                    processed.append((noun, pos))
                    processed.append((josa, map_josa_to_kkma_tag(josa)))
                    if DEBUG_MORPHS:
                        print(f"[DEBUG]   분리됨: '{word}' -> ('{noun}', {pos}), ('{josa}', {map_josa_to_kkma_tag(josa)})")
                    continue

        processed.append((word, pos))
    
    if DEBUG_MORPHS:
        print(f"[DEBUG] 처리된 결과: {processed}")
        print(f"[DEBUG] ===== 형태소 분석 종료 =====\n")
    
    return processed

def extract_nouns_adjectives_verbs(sentence, tokens=None, default_subject=None):
    """한 문장에서 명사, 형용사, 동사를 추출하여 동사 관계를 생성
    
    Args:
        sentence: 분석할 문장
        tokens: 형태소 분석 결과 (None이면 자동 분석)
        default_subject: Q에서 찾은 주어 (A에서 주어가 없을 때 사용)
    """
    global last_subject
    
    # [Level 3] PII 마스킹 및 위험어 차단 로직 적용
    sentence = sentence.strip()
    sentence = mask_pii(sentence)
    
    if DEBUG_MORPHS:
        print(f"[DEBUG] predicate_nouns 리스트: {predicate_nouns}")
    
    # 형태소 분석 (tokens가 없을 때만 수행)
    if tokens is None:
        tokens = postprocess_kkma_pos(sentence)
    
    # [Level 3] 위험어 필터링 (분석 결과 기반)
    if not is_safe_tokens(tokens):
        return {"error": "DANGER_WORD_DETECTED"}
    
    noun_dict = {}  
    noun_order = []  
    relations = []  
    current_adjectives = []
    noun_stack = []  # [변경] 스택 구조로 명사 우선순위 관리
    current_subject = None  
    current_indirect_object = None  
    connected_nouns = []
    
    # [추가] default_subject가 있으면 스택에 초기화로 추가 (가장 낮은 우선순위)
    if default_subject is not None:
        noun_stack.append(default_subject)
        current_subject = default_subject
        if DEBUG_MORPHS:
            print(f"[DEBUG] default_subject 초기화: '{default_subject.noun}'")
    
    all_adjectives = []
    all_verbs = []
    all_positions = []
    etd_pending = False  # [추가] ETD 감지: 다음 명사가 주어가 됨
    etd_verb = None  # [추가] ETD 앞의 동사 저장
    etd_object = None  # [추가] ETD 앞의 목적어 저장
    
    i = 0
    while i < len(tokens):
        word, pos = tokens[i]
        
        # [Level 1] 형용사 수집 및 원형 복원
        if pos in ['VA'] or word.endswith("색") or word.endswith("빛"):  
            base_word = get_base_form(word, pos) if pos == 'VA' else word
            current_adjectives.append(base_word)
            all_adjectives.append(base_word)
            if DEBUG_MORPHS:
                print(f"[DEBUG] 형용사 추출: '{word}' -> 원형: '{base_word}'")
        
        # [Level 1] ETD (관형사형 어미) 처리: "쓴", "ㄴ", "할" 등
        # "모자를 쓴 아이" → 아이가 쓴 주어
        elif pos == 'ETD':
            # [수정] ETD 전 토큰이 동사일 때만 etd_pending 설정
            if i > 0:
                prev_word, prev_pos = tokens[i - 1]
                if prev_pos in ['VV', 'VXV']:  # 동사인 경우만
                    etd_pending = True
                    etd_verb = get_base_form(prev_word, prev_pos)
                    # 목적어: noun_order의 마지막 명사
                    if len(noun_order) > 0:
                        etd_object = noun_dict[noun_order[-1]]
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] ETD 감지 (동사 있음): '{word}' (동사: '{etd_verb}', 목적어: '{etd_object.noun if etd_object else None}')")
            
            # [수정] 형용사는 유지 (술어명사 처리를 위해)
        
        # 명사 처리
        elif pos.startswith('N'):
            # [수정] 매 명사마다 position_target_noun 초기화 (이전 위치명사 정보 초기화)
            position_target_noun = None
            
            if DEBUG_MORPHS:
                print(f"[DEBUG] 명사 처리: '{word}', predicate_nouns: {predicate_nouns}, word in predicate_nouns: {word in predicate_nouns}")
            
            # [추가] 술어명사(predicate noun) 처리
            # "구름은 둥근 모양입니다" → "모양"은 술어명사이므로, 형용사들을 "구름"에 추가
            if word in predicate_nouns:
                if current_subject is not None:
                    # 현재 형용사들을 주어에 추가
                    for adj in current_adjectives:
                        if adj not in current_subject.adjectives:
                            current_subject.adjectives.append(adj)
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 술어명사 감지: '{word}' -> '{current_subject.noun}'에 형용사들 {current_adjectives} 추가")
                current_adjectives = []
                # 술어명사는 저장하지 않음 (스택에 추가하지 않음)
            elif word not in positions:
                # 일반 명사만 저장
                if word not in noun_dict:
                    new_noun = imagine(word, current_adjectives.copy())
                    noun_dict[word] = new_noun
                    noun_order.append(word)
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 명사 등록: '{word}'")
                else:
                    # [수정] 형용사 중복 제외하고 추가
                    for adj in current_adjectives:
                        if adj not in noun_dict[word].adjectives:
                            noun_dict[word].adjectives.append(adj)
                
                noun_stack.append(noun_dict[word])  # [변경] 스택에 추가
            else:
                # [변경] 위치 명사 감지: 스택에서 제거 (위치 정보를 저장할 기준 명사)
                position_target_noun = None  # [추가] 위치의 대상이 되는 명사를 임시 저장
                if noun_stack:
                    popped_noun = noun_stack.pop()
                    position_target_noun = popped_noun  # [추가] 위치 저장 시 사용하기 위해 임시 저장
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 위치 명사 감지: '{word}' -> 스택에서 '{popped_noun.noun}' 제거")
                else:
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 위치 명사 감지: '{word}' (스택이 비어있음)")
            
            # 다음 토큰이 있으면 조사 확인
            if i + 1 < len(tokens):
                next_word, next_pos = tokens[i + 1]
                
                if next_pos == 'JKO':  # 목적어 조사
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                        noun_order.append(word)
                    noun_stack.append(noun_dict[word])  # [변경] 스택에 추가
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 명사(목적어) 추출: '{word}' + '{next_word}'")
                    current_adjectives = []
                    i += 2  
                    continue
                
                elif next_pos == 'JKM':  # 위치/방향 조사
                    # [수정] "와", "과", "랑"은 연결 조사이므로 일반 명사로 처리
                    if next_word in ['와', '과', '랑']:
                        # 연결 조사는 위치 저장 안함
                        current_adjectives = []
                        i += 2
                        continue
                    
                    if next_word in ['에게', '에게서']:
                        if word not in noun_dict:
                            new_noun = imagine(word)
                            noun_dict[word] = new_noun
                            noun_order.append(word)
                        current_indirect_object = noun_dict[word]
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 간접목적어 추출: '{word}' + '{next_word}'")
                    else:
                        # 위치 저장
                        subject_for_position = current_subject if current_subject is not None else default_subject
                        
                        # [변경] 위치명사로 pop된 명사를 사용 (position_target_noun)
                        # 만약 position_target_noun이 없으면 noun_stack의 최상위 명사 사용
                        if position_target_noun is not None:
                            current_noun_for_position = position_target_noun
                        else:
                            current_noun_for_position = noun_stack[-1] if noun_stack else None
                        
                        if current_noun_for_position is not None and subject_for_position is not None:
                            position_info = (current_noun_for_position.noun, word)
                            # [수정] 중복 체크: 이미 저장된 위치는 다시 저장하지 않음
                            if position_info not in subject_for_position.position:
                                all_positions.append(position_info)
                                subject_for_position.position.append(position_info)
                                if DEBUG_MORPHS:
                                    print(f"[DEBUG] 위치 저장: '{subject_for_position.noun}'에 {position_info} 추가")
                            else:
                                if DEBUG_MORPHS:
                                    print(f"[DEBUG] 위치 저장 중복 방지: {position_info}")
                        else:
                            if DEBUG_MORPHS:
                                print(f"[DEBUG] 위치 저장 실패: current_noun={current_noun_for_position.noun if current_noun_for_position else None}, subject={subject_for_position.noun if subject_for_position else None}")
                    i += 2  
                    continue
                
                elif next_pos.startswith('JK'):  # 주어
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                        noun_order.append(word)
                    else:
                        # [수정] 형용사 중복 제외하고 추가
                        for adj in current_adjectives:
                            if adj not in noun_dict[word].adjectives:
                                noun_dict[word].adjectives.append(adj)
                    noun_stack.append(noun_dict[word])  # [변경] 스택에 추가
                    current_subject = noun_dict[word]
                    last_subject = current_subject  # 전역 변수 업데이트
                    connected_nouns = [current_subject]
                    
                    # [추가] ETD 처리: 저장된 동사/목적어 정보로 관계 생성
                    if etd_pending and etd_verb is not None:
                        negation = detect_negation(tokens, i - 1)  # ETD 위치 기준
                        subject = current_subject
                        obj = etd_object
                        
                        relation = Relation(subject, etd_verb, obj, negation=negation)
                        relations.append(relation)
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] ETD 관계 생성: {relation}")
                        
                        etd_pending = False
                        etd_verb = None
                        etd_object = None
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 명사(주어) 추출: '{word}' + '{next_word}' (last_subject 업데이트)")
                    current_adjectives = []
                    i += 2  
                    continue
            
            # [수정] 조사 없는 경우 또는 처리되지 않은 조사의 경우
            # (이미 위에서 명사를 noun_dict에 등록하고 스택에 추가했으므로 추가 처리 불필요)
            if DEBUG_MORPHS:
                print(f"[DEBUG] 명사 '{word}' 처리 완료 (조사: {tokens[i+1][1] if i+1 < len(tokens) else 'None'})")
            current_adjectives = []
        
        # [Level 1] 동사 처리 및 원형 복원
        elif pos in ['VV', 'VXV']:  
            base_verb = get_base_form(word, pos)  # KKMA pos 전달
            negation = detect_negation(tokens, i)  # 부정 표현 감지
            all_verbs.append(base_verb)
            if DEBUG_MORPHS:
                print(f"[DEBUG] 동사 추출: '{word}' (pos: {pos}) -> 원형: '{base_verb}'")
                print(f"[DEBUG] 사전 확인: '{base_verb}' in {list(verb_patterns.keys())} = {base_verb in verb_patterns}")
            
            # [추가] 다음 토큰이 ETD인지 확인 - ETD면 관계 생성하지 않음
            is_etd_next = (i + 1 < len(tokens) and tokens[i + 1][1] == 'ETD')
            if is_etd_next:
                if DEBUG_MORPHS:
                    print(f"[DEBUG] 다음이 ETD이므로 관계 생성 보류")
            else:
                # ETD가 아니면 일반 동사 처리
            
                # [변경] 먼저 기본 요소(주어, 동사, 목적어)가 충분한지 확인
                subject_for_relation = current_subject
                if subject_for_relation is None and len(noun_order) > 0:
                    subject_for_relation = noun_dict[noun_order[0]]
                
                # [수정] 조사로 역할이 명시적이면 사전 확인 불필요
                # needs_dictionary는 항상 False로 유지 (조사가 이미 역할을 명확하게 함)
                needs_dictionary = False
                
                # 기본 요소가 충분하고 사전 확인 불필요하면 즉시 관계 생성
                # [수정] 주어 이외의 명사가 없을 때만 즉시 관계 생성 (목적어가 있으면 사전 확인)
                if (subject_for_relation is not None and 
                    len(noun_order) <= 1 and  # 주어만 있거나 명사 없음
                    not needs_dictionary):
                    # 주어만 있고 목적어 없는 경우
                    relation = Relation(subject_for_relation, base_verb, None, indirect_object=current_indirect_object, negation=negation)
                    relations.append(relation)
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 관계 생성(주어만 있음): {relation}")
                    current_indirect_object = None
                # 사전 확인이 필요하면
                elif base_verb in verb_patterns:
                    pattern = verb_patterns[base_verb]
                    nouns_in_order = [noun_dict[n] for n in noun_order]
                    
                    subject = None
                    indirect_object = None
                    obj = None
                    
                    # [수정] 현재 주어가 이미 설정되어 있으면 그것을 우선 사용
                    if current_subject is not None and "subject" in pattern:
                        subject = current_subject
                    
                    # 패턴에 따라 필요한 명사들을 할당 (주어 제외)
                    noun_idx = 0
                    for role in pattern:
                        if role == "subject":
                            # 이미 subject 처리됨
                            noun_idx += 1
                        elif role == "indirect_object":
                            if noun_idx < len(nouns_in_order):
                                indirect_object = nouns_in_order[noun_idx]
                                noun_idx += 1
                        elif role == "object":
                            if noun_idx < len(nouns_in_order):
                                obj = nouns_in_order[noun_idx]
                                noun_idx += 1
                    
                    # subject가 필요한데 없으면 default_subject → last_subject → current_subject → "나" 순서로 사용
                    if "subject" in pattern and subject is None:
                        if default_subject is not None:
                            subject = default_subject
                            if DEBUG_MORPHS:
                                print(f"[DEBUG] default_subject 사용: '{subject.noun}'")
                        elif last_subject is not None:
                            subject = last_subject
                        elif current_subject is not None:
                            subject = current_subject
                        else:
                            # 기본값으로 "나"를 주어로 사용
                            if "나" not in noun_dict:
                                subject = imagine("나")
                                noun_dict["나"] = subject
                            else:
                                subject = noun_dict["나"]
                    
                    # [수정] 주어만 있으면 관계 생성 (목적어/간접목적어는 선택사항)
                    # 목적어가 필요 없는 동사는 이미 pattern에서 정의되어 있음
                    if subject is not None:
                        relation = Relation(subject, base_verb, obj, indirect_object=indirect_object, negation=negation)
                        relations.append(relation)
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 관계 생성(사전 매핑): {relation}")
        
        # 종결어미 처리
        elif pos.startswith('EF'):
            # [변경] 스택의 최상위 명사에 형용사 추가 (중복 제외)
            if current_adjectives and noun_stack:
                for adj in current_adjectives:
                    if adj not in noun_stack[-1].adjectives:
                        noun_stack[-1].adjectives.append(adj)
                current_adjectives = []
            connected_nouns = []
            noun_stack = []  # [변경] 문장 끝에 스택 리셋
            current_subject = None
            current_indirect_object = None
            if DEBUG_MORPHS:
                print(f"[DEBUG] 종결어미 감지: '{word}' (스택 리셋)")
        
        i += 1
    
    # [변경] 함수 끝: 남아있는 형용사를 스택의 마지막 명사에 추가 (중복 제외)
    if current_adjectives and noun_stack:
        for adj in current_adjectives:
            if adj not in noun_stack[-1].adjectives:
                noun_stack[-1].adjectives.append(adj)
    
    if DEBUG_MORPHS:
        print(f"\n[DEBUG] ===== 추출 결과 =====")
        print(f"[DEBUG] noun_dict 키: {list(noun_dict.keys())}")
        print(f"[DEBUG] noun_order: {noun_order}")
        print(f"[DEBUG] 추출된 형용사들: {all_adjectives}")
        print(f"[DEBUG] 추출된 동사들: {all_verbs}")
        print(f"[DEBUG] 추출된 위치들: {all_positions}")
        print(f"[DEBUG] 생성된 관계: {relations}")
        print(f"[DEBUG] ===== 추출 완료 =====\n")
    
    if not noun_dict:
        return {
            'adjectives': all_adjectives,
            'verbs': all_verbs,
            'position': all_positions
        }
    
    # [단순화] noun_dict의 모든 명사를 그냥 반환 (순서: noun_dict의 순서)
    noun_objects = list(noun_dict.values())
    
    if DEBUG_MORPHS:
        print(f"[DEBUG] 반환하는 noun_objects: {[n.noun for n in noun_objects]}")
    
    return {
        'noun_objects': noun_objects,
        'relations': relations
    }


def extract_pos(text):
    text = text.strip()
    tokens = postprocess_kkma_pos(text)
    if DEBUG_MORPHS:
        print(f"\n[DEBUG] ===== extract_pos =====")
        print(f"[DEBUG] 입력: {text}")
        print(f"[DEBUG] 품사 태깅: {tokens}")
        print(f"[DEBUG] ===== 완료 =====\n")
    return [tokens]

def extract_morphs(text):
    text = text.strip()
    morphs = kkma.morphs(text)
    if DEBUG_MORPHS:
        print(f"\n[DEBUG] ===== extract_morphs =====")
        print(f"[DEBUG] 입력: {text}")
        print(f"[DEBUG] 형태소: {morphs}")
        print(f"[DEBUG] ===== 완료 =====\n")
    return [morphs]

def extract_nouns_only(text, tokens=None):
    text = text.strip()
    if tokens is None:
        tokens = postprocess_kkma_pos(text)
    result_nouns = []
    for word, pos in tokens:
        if pos.startswith('N'):
            result_nouns.append(word)
    return result_nouns

def split_sentences_by_connective(text):
    text = text.strip()
    kkma_tokens = postprocess_kkma_pos(text)
    
    # 토큰 인덱스 기반 분리 위치 찾기 (EC 또는 MAJ 후)
    split_indices = []
    for i, (word, pos) in enumerate(kkma_tokens):
        if pos.startswith('EC') or pos == 'MAJ':
            split_indices.append(i + 1)
    
    if not split_indices:
        # 분리할 부분이 없으면 원문 전체
        return [(text, kkma_tokens)]
    
    # 토큰들의 원본 텍스트 위치 추적
    token_char_positions = []
    search_start = 0
    
    for word, _ in kkma_tokens:
        pos = text.find(word, search_start)
        if pos != -1:
            token_char_positions.append(pos)
            search_start = pos + len(word)
        else:
            token_char_positions.append(-1)
    
    # 토큰 범위로 분리된 텍스트 추출
    result = []
    start_idx = 0
    
    for end_idx in split_indices:
        group_tokens = kkma_tokens[start_idx:end_idx]
        
        # 시작 문자 위치
        start_char = token_char_positions[start_idx] if start_idx < len(token_char_positions) and token_char_positions[start_idx] != -1 else 0
        
        # 마지막 토큰의 종료 위치
        last_token_idx = end_idx - 1
        if last_token_idx >= 0 and last_token_idx < len(token_char_positions) and token_char_positions[last_token_idx] != -1:
            end_char = token_char_positions[last_token_idx] + len(kkma_tokens[last_token_idx][0])
        else:
            end_char = len(text)
        
        group_text = text[start_char:end_char].strip()
        if group_text:
            result.append((group_text, group_tokens))
        
        start_idx = end_idx
    
    # 마지막 그룹
    if start_idx < len(kkma_tokens):
        start_char = token_char_positions[start_idx] if start_idx < len(token_char_positions) and token_char_positions[start_idx] != -1 else 0
        group_tokens = kkma_tokens[start_idx:]
        group_text = text[start_char:].strip()
        if group_text:
            result.append((group_text, group_tokens))
    
    return result

def load_qa_from_json(json_file):
    import json
    with open(json_file, 'r', encoding='utf-8') as f:
        return json.load(f)

if __name__ == "__main__":
    # JSON 파일이 실제로 존재하지 않으면 오류가 나므로 예외처리 블록을 추가하거나 주석처리하여 테스트하세요.
    try:
        conversations = load_qa_from_json("conversations.json")
        
        if not conversations[0].get('text', "").startswith("Q"):    
            print("=" * 60)
            print("[ 명사, 형용사, 동사 추출 결과 ]")
            print("=" * 60)
            
            for i, conversation in enumerate(conversations, 1):
                text = conversation.get('text', "")
                print(f"\n원문 [{i}]: {text}")
                
                split_result = split_sentences_by_connective(text)
                sentences = [s for s, _ in split_result]
                print(f"분리된 문장: {sentences}")
                
                print("\n📝 추출 결과:")
                for j, (sentence, tokens) in enumerate(split_result, 1):
                    result = extract_nouns_adjectives_verbs(sentence, tokens)
                    print(f"  [{j}] {sentence}")
                    
                    # [Level 3] 위험어 감지 시 스킵 로직
                    if result.get("error") == "DANGER_WORD_DETECTED":
                        print("      [주의] 임상적 위험어가 포함되어 필터링 되었습니다.")
                        continue
                        
                    if 'noun_objects' in result: 
                            noun_objects = result['noun_objects']
                            relations = result['relations']
                            
                            print(f"      명사 객체들:")
                            for noun_obj in noun_objects:
                                print(f"        - {noun_obj.noun}", end="")
                                if noun_obj.adjectives:
                                    print(f" (형용사: {noun_obj.adjectives})", end="")
                                if noun_obj.position:
                                    print(f" (위치: {noun_obj.position})", end="")
                                print()
                            
                            print(f"      동사 관계:")
                            for relation in relations:
                                print(f"        - {relation}")
                    else: 
                            print(f"      형용사: {result.get('adjectives', [])}")
                            print(f"      동사: {result.get('verbs', [])}")
                            print(f"      위치 정보: {result.get('position', [])}")
            print("\n" + "=" * 60)
            
        else:
            print("질문-답변 형식이 감지되었습니다. 질문과 답변을 분리하여 출력합니다.")
            seting = conversations[0:2]
            conversations = conversations[2:]
            seting_text = seting[1].get('text', "")
            seting_text = seting_text[2:].strip()
            seting_tokens = postprocess_kkma_pos(seting_text)
            result = extract_nouns_adjectives_verbs(seting_text, seting_tokens)
            
            if 'noun_objects' in result:
                nouns = result['noun_objects']
            else:
                nouns = []
            
            print(f"설정 명사 객체: {nouns}")
            current_noun = None
            all_relations = []  # [추가] 모든 관계를 수집할 리스트
            
            for i, conversation in enumerate(conversations, 1):
                text = conversation.get('text', "")
                if text.startswith("Q:"):
                    # [Q 처리] 주어를 찾는 로직
                    current_noun = None
                    text = text[2:].strip()
                    q_tokens = postprocess_kkma_pos(text)
                    
                    # 주어 추출: 조사 JKS(이/가)를 가진 명사를 주어로 인식
                    for j in range(len(q_tokens) - 1):
                        word, pos = q_tokens[j]
                        next_word, next_pos = q_tokens[j + 1]
                        if pos.startswith('N') and next_pos == 'JKS':  # 주어 조사
                            # 설정된 명사 중에서 일치하는 것을 찾기
                            for noun_obj in nouns:
                                if word == noun_obj.noun:
                                    current_noun = noun_obj
                                    if DEBUG_MORPHS:
                                        print(f"[DEBUG] Q에서 주어 추출: '{word}'")
                                    break
                            if current_noun is not None:
                                break
                    
                    # 주어를 찾지 못한 경우 첫 번째 명사 사용
                    if current_noun is None:
                        extracted_items = extract_nouns_only(text, q_tokens)
                        for noun_str in extracted_items:
                            for noun_obj in nouns:
                                if noun_str == noun_obj.noun:
                                    current_noun = noun_obj
                                    break
                            if current_noun is not None:
                                break
                    
                    if current_noun is not None:
                        print(f"\n[Q {i}] {text}")
                        print(f"  → 선택된 주어: {current_noun.noun}")
                
                elif text.startswith("A:"):
                    # [A 처리] 한 줄 분석: extract_nouns_adjectives_verbs 사용
                    # Q에서 찾은 주어를 default_subject로 전달
                    text = text[2:].strip()
                    answer_tokens = postprocess_kkma_pos(text)
                    result = extract_nouns_adjectives_verbs(text, answer_tokens, default_subject=current_noun)
                    
                    if result.get("error") == "DANGER_WORD_DETECTED":
                        print(f"[A {i}] {text}")
                        print("  [주의] 임상적 위험어 필터링됨")
                        continue

                    print(f"[A {i}] {text}")
                    
                    if 'noun_objects' in result: 
                            # 관계와 형용사 모두 추출
                            extract_nouns_list = result['noun_objects']
                            relations = result.get('relations', [])
                            
                            # [추가] A에서 추출된 새로운 명사들을 nouns 리스트에 추가
                            # (이후 Q에서 그 명사를 찾을 수 있도록)
                            for noun_obj in extract_nouns_list:
                                existing_noun = None
                                for n in nouns:
                                    if n.noun == noun_obj.noun:
                                        existing_noun = n
                                        break
                                
                                if existing_noun is None:
                                    # 새로운 명사이면 추가
                                    nouns.append(noun_obj)
                                    if DEBUG_MORPHS:
                                        print(f"[DEBUG] 새로운 명사 추가: '{noun_obj.noun}'")
                                else:
                                    # 기존 명사면 형용사 병합 (중복 제외)
                                    for adj in noun_obj.adjectives:
                                        if adj not in existing_noun.adjectives:
                                            existing_noun.adjectives.append(adj)
                                            if DEBUG_MORPHS:
                                                print(f"[DEBUG] 형용사 추가: '{existing_noun.noun}'에 '{adj}' 추가")
                            
                            # 현재 주어가 있으면 형용사 추가
                            if current_noun is not None:
                                for noun_obj in extract_nouns_list:
                                    # [주석] extract_nouns_adjectives_verbs()에서만 처리하도록 변경
                                    # current_noun.adjectives.extend(noun_obj.adjectives)
                                    # [개선] 위치 정보 병합 (중복 제외)
                                    if noun_obj.position:
                                        for pos_info in noun_obj.position:
                                            if pos_info not in current_noun.position:
                                                current_noun.position.append(pos_info)
                                            elif DEBUG_MORPHS:
                                                print(f"[DEBUG] A 처리에서 위치 중복 방지: {pos_info}")
                                
                                # 관계 출력 및 저장
                                if relations:
                                    print(f"  추출된 관계: {relations}")
                                    all_relations.extend(relations)  # [추가] 관계 저장
                                else:
                                    print(f"  추출된 형용사: {current_noun.adjectives}")
                            else:
                                # 주어가 없으면 A에서 추출된 첫 번째 명사를 현재 명사로 설정
                                if extract_nouns_list:
                                    current_noun = extract_nouns_list[0]
                                    print(f"  [주어 자동 설정] '{current_noun.noun}'")
                                    # 관계 출력 및 저장 (있으면)
                                    if relations:
                                        print(f"  추출된 관계: {relations}")
                                        all_relations.extend(relations)  # [추가] 관계 저장
                                else:
                                    print(f"  [경고] 매칭되는 주어가 없습니다 (하지만 명사는 저장됨)")
                    else: 
                        # 형용사/동사만 추출된 경우
                        if current_noun is not None:
                            adjectives = result.get('adjectives', [])
                            if DEBUG_MORPHS:
                                print(f"[DEBUG] A 처리 (756줄): current_noun='{current_noun.noun}', adjectives={adjectives}")
                            # [주석] extract에서 이미 default_subject에 추가됨
                            # current_noun.adjectives.extend(adjectives)
                            print(f"  추출된 형용사: {adjectives}")
            
            print("\n" + "=" * 60)
            print("최종 명사 객체:")
            print("=" * 60)
            for noun_obj in nouns:
                noun_obj.print_all()
            
            print("\n" + "=" * 60)
            print("최종 관계 객체:")
            print("=" * 60)
            if all_relations:
                for idx, relation in enumerate(all_relations, 1):
                    print(f"[{idx}] {relation}")
            else:
                print("(추출된 관계가 없습니다)")
    except FileNotFoundError:
        print("conversations.json 파일이 없어 실행을 종료합니다.")


