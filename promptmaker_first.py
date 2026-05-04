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
positions = ["왼쪽", "오른쪽", "위", "아래", "앞", "뒤","전","후","안","밖","속","겉","옆"] # 상대적 위치 저장
last_subject = None  # 가장 최근에 나왔던 주어 저장

# [Level 3] 회상 치료 특화 위험어 사전 (추가됨)
DANGER_WORDS = ["죽", "피", "때리", "부러지", "자살", "사고", "다치", "훔치"]

def is_safe_sentence(text):
    """[Level 3] 위험어 필터링: 문장에 위험한 단어가 포함되어 있는지 확인"""
    for danger in DANGER_WORDS:
        if danger in text:
            return False
    return True

def mask_pii(text):
    """[Level 3] 개인정보(PII) 비식별화: 전화번호, 주민번호 등 마스킹"""
    # 휴대폰 번호 마스킹 (예: 010-1234-5678 -> 010-****-****)
    text = re.sub(r'01[016789]-?\d{3,4}-?\d{4}', '010-****-****', text)
    # 주민등록번호 마스킹
    text = re.sub(r'\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-?[1-4]\d{6}', '******-*******', text)
    return text

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
        self.position = [] # (상대적 위치,물체)로 저장하기 위한 리스트
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
        print(f"위치: {self.position}")
        print(f"수식하는 명사들: {self.moderators}")
    
    def __repr__(self):
        return f"<{self.noun}: {self.adjectives} {self.position} Moderators: {self.moderators}>"

class Relation:
    """[Level 2] 동사 중심 관계를 저장하는 클래스: (주어, 원형 동사, 목적어, [간접목적어])"""
    def __init__(self, subject, verb, obj, indirect_object=None):
        self.subject = subject  # imagine 객체 (주어)
        self.verb = verb  # 동사 (원형)
        self.obj = obj  # imagine 객체 (목적어)
        self.indirect_object = indirect_object  # imagine 객체 (간접목적어, 선택사항)
    
    def __repr__(self):
        obj_noun = self.obj.noun if self.obj else "None"
        indirect_str = f" - {self.indirect_object.noun}에게" if self.indirect_object else ""
        return f"({self.subject.noun} {self.verb} {obj_noun}){indirect_str}"
    
    def print_relation(self):
        obj_noun = self.obj.noun if self.obj else "None"
        indirect_str = f" - {self.indirect_object.noun}에게" if self.indirect_object else ""
        print(f"({self.subject.noun} {self.verb} {obj_noun}){indirect_str}")

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

def extract_nouns_adjectives_verbs(sentence, tokens=None):
    """한 문장에서 명사, 형용사, 동사를 추출하여 동사 관계를 생성"""
    global last_subject
    
    # [Level 3] PII 마스킹 및 위험어 차단 로직 적용
    sentence = sentence.strip()
    sentence = mask_pii(sentence)
    
    if not is_safe_sentence(sentence):
        # 딕셔너리로 반환하되, 플래그를 통해 안전하지 않음을 상위에서 인지하도록 함
        return {"error": "DANGER_WORD_DETECTED"}

    # 형태소 분석 (tokens가 없을 때만 수행)
    if tokens is None:
        tokens = postprocess_kkma_pos(sentence)
    
    noun_dict = {}  
    noun_order = []  
    relations = []  
    current_adjectives = []
    current_noun = None
    current_subject = None  
    current_indirect_object = None  
    connected_nouns = []  
    
    all_adjectives = []
    all_verbs = []
    all_positions = []
    
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
        
        # 명사 처리
        elif pos.startswith('N'):
            if i + 1 < len(tokens):
                next_word, next_pos = tokens[i + 1]
                
                if next_pos == 'JKO':  # 목적어
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                        noun_order.append(word)
                    current_noun = noun_dict[word]
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 명사(목적어) 추출: '{word}' + '{next_word}'")
                    current_adjectives = []
                    i += 2  
                    continue
                
                elif next_pos == 'JKM':  # 위치/방향 조사
                    if next_word in ['에게', '에게서']:
                        if word not in noun_dict:
                            new_noun = imagine(word)
                            noun_dict[word] = new_noun
                            noun_order.append(word)
                        current_indirect_object = noun_dict[word]
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 간접목적어 추출: '{word}' + '{next_word}'")
                    else:
                        all_positions.append(word)
                        if current_noun is not None:
                            current_noun.position.append(word)
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 위치 추출: '{word}' + '{next_word}'")
                    i += 2  
                    continue
                
                elif next_pos.startswith('JK'):  # 주어
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                        noun_order.append(word)
                    else:
                        noun_dict[word].adjectives.extend(current_adjectives)
                    current_noun = noun_dict[word]
                    current_subject = current_noun
                    last_subject = current_noun  # 전역 변수 업데이트
                    connected_nouns = [current_noun]
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 명사(주어) 추출: '{word}' + '{next_word}' (last_subject 업데이트)")
                    current_adjectives = []
                    i += 2  
                    continue
            
            # 일반 명사
            if word not in noun_dict:
                new_noun = imagine(word, current_adjectives.copy())
                noun_dict[word] = new_noun
                noun_order.append(word)
            else:
                noun_dict[word].adjectives.extend(current_adjectives)
            
            current_noun = noun_dict[word]
            connected_nouns.append(current_noun)
            if DEBUG_MORPHS:
                print(f"[DEBUG] 일반 명사 추출: '{word}'")
            current_adjectives = []
        
        # [Level 1] 동사 처리 및 원형 복원
        elif pos in ['VV', 'VXV']:  
            base_verb = get_base_form(word, pos)  # KKMA pos 전달
            all_verbs.append(base_verb)
            if DEBUG_MORPHS:
                print(f"[DEBUG] 동사 추출: '{word}' (pos: {pos}) -> 원형: '{base_verb}'")
                print(f"[DEBUG] 사전 확인: '{base_verb}' in {list(verb_patterns.keys())} = {base_verb in verb_patterns}")
            
            # 동사 사전 확인 (원형 기준으로 매핑)
            if base_verb in verb_patterns:
                pattern = verb_patterns[base_verb]
                nouns_in_order = [noun_dict[n] for n in noun_order]
                
                subject = None
                indirect_object = None
                obj = None
                
                # 패턴에 따라 필요한 명사들을 할당
                for idx, role in enumerate(pattern):
                    if idx < len(nouns_in_order):
                        if role == "subject":
                            subject = nouns_in_order[idx]
                        elif role == "indirect_object":
                            indirect_object = nouns_in_order[idx]
                        elif role == "object":
                            obj = nouns_in_order[idx]
                
                # subject가 필요한데 없으면 last_subject 사용, 그것도 없으면 "나" 사용
                if "subject" in pattern and subject is None:
                    if last_subject is not None:
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
                
                # 패턴에 필요한 모든 요소가 있는지 확인
                has_all_required = True
                for role in pattern:
                    if role == "subject" and subject is None:
                        has_all_required = False
                        break
                    elif role == "indirect_object" and indirect_object is None:
                        has_all_required = False
                        break
                    elif role == "object" and obj is None:
                        has_all_required = False
                        break
                
                if has_all_required:
                    relation = Relation(subject, base_verb, obj, indirect_object=indirect_object)
                    relations.append(relation)
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 관계 생성(사전 매핑): {relation}")
            else:
                # 사전에 없으면 기존 로직 사용
                subject_for_relation = current_subject
                if subject_for_relation is None and len(noun_order) > 0:
                    subject_for_relation = noun_dict[noun_order[0]]
                
                if subject_for_relation is not None and current_noun is not None:
                    if current_noun != subject_for_relation:
                        relation = Relation(subject_for_relation, base_verb, current_noun, indirect_object=current_indirect_object)
                        relations.append(relation)
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 관계 생성(기본 로직): {relation}")
                        current_indirect_object = None
        
        # 종결어미 처리
        elif pos.startswith('EF'):
            if current_adjectives and current_noun is not None:
                current_noun.adjectives.extend(current_adjectives)
                current_adjectives = []
            connected_nouns = []
            current_subject = None
            current_indirect_object = None
            if DEBUG_MORPHS:
                print(f"[DEBUG] 종결어미 감지: '{word}'")
        
        i += 1
    
    if current_adjectives and current_noun is not None:
        current_noun.adjectives.extend(current_adjectives)
    
    if DEBUG_MORPHS:
        print(f"\n[DEBUG] ===== 추출 결과 =====")
        print(f"[DEBUG] 추출된 명사들: {list(noun_dict.keys())}")
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
    
    noun_objects = [noun_dict[noun] for noun in noun_order]
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
                    if isinstance(result, dict) and result.get("error") == "DANGER_WORD_DETECTED":
                        print("      [주의] 임상적 위험어가 포함되어 필터링 되었습니다.")
                        continue
                        
                    if isinstance(result, dict):
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
            
            if isinstance(result, dict) and 'noun_objects' in result:
                nouns = result['noun_objects']
            else:
                nouns = []
            
            print(f"설정 명사 객체: {nouns}")
            current_noun = None
            
            for i, conversation in enumerate(conversations, 1):
                text = conversation.get('text', "")
                if text.startswith("Q:"):
                    current_noun = None
                    text = text[2:].strip()
                    q_tokens = postprocess_kkma_pos(text)
                    extracted_items = extract_nouns_only(text, q_tokens)
                    for noun_str in extracted_items:
                        for noun_obj in nouns:
                            if noun_str == noun_obj.noun:
                                current_noun = noun_obj
                                break
                        if current_noun is not None:
                            break
                elif text.startswith("A:"):
                    text = text[2:].strip()
                    answer_tokens = postprocess_kkma_pos(text)
                    result = extract_nouns_adjectives_verbs(text, answer_tokens)
                    
                    if isinstance(result, dict) and result.get("error") == "DANGER_WORD_DETECTED":
                        print("  [주의] 임상적 위험어 필터링됨")
                        continue

                    if current_noun is not None:
                        if isinstance(result, dict):
                            if 'noun_objects' in result: 
                                extract_nouns_list = result['noun_objects']
                                for noun_obj in extract_nouns_list:
                                    current_noun.adjectives.extend(noun_obj.adjectives)
                            else: 
                                adjectives = result.get('adjectives', [])
                                current_noun.adjectives.extend(adjectives)
            
            print("\n최종 명사 객체:")
            for noun_obj in nouns:
                noun_obj.print_all()
    except FileNotFoundError:
        print("conversations.json 파일이 없어 실행을 종료합니다.")


