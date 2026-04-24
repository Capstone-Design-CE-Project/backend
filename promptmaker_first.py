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

kkma = Kkma()
okt = Okt()
nouns = []#나왔던 명사 저장
positions = ["왼쪽", "오른쪽", "위", "아래", "앞", "뒤","전","후","안","밖","속","겉","옆"]#상대적 위치 저장

# 동사 사전: 동사 형태소(어미 제거) → 필요한 역할들
verb_patterns = {
    "먹이": ["subject", "indirect_object", "object"],
    "주": ["subject", "indirect_object", "object"],
    "먹": ["subject", "object"],
    "보": ["subject", "object"],
}

class imagine:
    """명사와 그 수식어(형용사)를 저장하는 클래스"""
    def __init__(self, noun, adjectives=None):
        self.noun = noun  # 명사
        self.adjectives = adjectives if adjectives else []  # 명사의 수식어(형용사)
        self.position = []# (상대적 위치,물체)로 저장하기 위한 리스트
        self.moderators = []  
        self.target = None
    
    def add_adjective(self, adjective):
        """형용사 추가"""
        self.adjectives.append(adjective)
    
    def print_noun(self):
        """명사 출력"""
        print(f"명사: {self.noun}")
    
    def print_adjectives(self):
        """형용사들 출력"""
        print(f"형용사: {self.adjectives}")
    
    def print_all(self):
        """명사, 형용사, 위치 모두 출력"""
        print(f"명사: {self.noun}")
        print(f"형용사: {self.adjectives}")
        print(f"위치: {self.position}")
        print(f"수식하는 명사들: {self.moderators}")
    
    def __repr__(self):
        return f"<{self.noun}: {self.adjectives} {self.position} Moderators: {self.moderators}>"

class Relation:
    """동사 중심 관계를 저장하는 클래스: (주어, 동사, 목적어, [간접목적어])"""
    def __init__(self, subject, verb, obj, indirect_object=None):
        self.subject = subject  # imagine 객체 (주어)
        self.verb = verb  # 동사
        self.obj = obj  # imagine 객체 (목적어)
        self.indirect_object = indirect_object  # imagine 객체 (간접목적어, 선택사항)
    
    def __repr__(self):
        if self.indirect_object:
            return f"({self.subject.noun} {self.verb} {self.obj.noun}) - {self.indirect_object.noun}에게"
        return f"({self.subject.noun} {self.verb} {self.obj.noun})"
    
    def print_relation(self):
        """관계 출력"""
        if self.indirect_object:
            print(f"({self.subject.noun} {self.verb} {self.obj.noun}) - {self.indirect_object.noun}에게")
        else:
            print(f"({self.subject.noun} {self.verb} {self.obj.noun})")
def map_josa_to_kkma_tag(josa: str) -> str:
	josa_tag_map = {
		"이": "JKS",
		"가": "JKS",
		"은": "JX",
		"는": "JX",
		"을": "JKO",
		"를": "JKO",
		"에": "JKM",
		"에서": "JKM",
		"에게": "JKM",
		"의": "JKG",
		"와": "JC",
		"과": "JC",
		"랑": "JC",
		"도": "JX",
		"만": "JX",
	}
	return josa_tag_map.get(josa, "JX")


def postprocess_kkma_pos(sentence: str):
	raw = kkma.pos(sentence)
	processed = []

	for word, pos in raw:
		if pos in {"NNG", "NNP"}:
			# Kkma가 명사+조사를 하나의 명사로 붙여 분석한 경우를 Okt로 보정한다.
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
					continue

		processed.append((word, pos))

	return processed
def extract_nouns_adjectives_verbs(sentence):
    """한 문장에서 명사, 형용사, 동사를 추출하여 동사 관계를 생성
    
    Args:
        sentence: 분석할 문장
    
    Returns:
        - 명사가 있으면: {'noun_objects': [imagine 객체들], 'relations': [Relation 객체들]}
        - 명사가 없으면: {'adjectives': [형용사들], 'verbs': [동사들], 'position': [위치들]}
    """
    sentence = sentence.strip()
    
    # 형태소 분석
    tokens = postprocess_kkma_pos(sentence)
    print(f"[DEBUG] 형태소 분석: {tokens}")  # 디버깅용
    
    # 명사별로 정보 저장
    noun_dict = {}  # {명사: imagine 객체}
    noun_order = []  # 명사 등장 순서
    relations = []  # (주어, 동사, 목적어) 관계
    current_adjectives = []
    current_noun = None
    current_subject = None  # 현재 문장의 주어 (동사와 연결할)
    current_indirect_object = None  # 현재 간접 목적어 (에게, 에서 등)
    connected_nouns = []  # 현재 절에서 접속사로 연결된 명사들
    
    # 명사가 없을 때를 위한 추적
    all_adjectives = []
    all_verbs = []
    all_positions = []
    
    i = 0
    while i < len(tokens):
        word, pos = tokens[i]
        
        # 형용사 수집
        if pos in ['VA'] or word.endswith("색") or word.endswith("빛"):  # 형용사 또는 색 명사
            current_adjectives.append(word)
            all_adjectives.append(word)  # 전체 형용사 추적
        
        # 명사 처리
        elif pos.startswith('N'):
            # 다음 토큰이 조사인지 확인
            if i + 1 < len(tokens):
                next_word, next_pos = tokens[i + 1]
                
                if next_pos == 'JKO':  # 목적격 조사 (을/를)
                    # 이 명사는 목적어
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                        noun_order.append(word)
                    current_noun = noun_dict[word]
                    current_adjectives = []
                    i += 2  # 조사도 스킵
                    continue
                
                elif next_pos == 'JKM':  # 위치/방향 조사 (에, 에서, 에게 등)
                    # 에게는 간접 목적어, 그 외는 위치 정보
                    if next_word in ['에게', '에게서']:
                        # 간접 목적어 설정
                        if word not in noun_dict:
                            new_noun = imagine(word)
                            noun_dict[word] = new_noun
                            noun_order.append(word)
                        current_indirect_object = noun_dict[word]
                    else:
                        # 위치 정보
                        all_positions.append(word)
                        if current_noun is not None:
                            current_noun.position.append(word)
                    i += 2  # 조사도 스킵
                    continue
                
                elif next_pos.startswith('JK'):  # 다른 조사 (이/가 등)
                    # 주어 명사
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                        noun_order.append(word)
                    else:
                        noun_dict[word].adjectives.extend(current_adjectives)
                    current_noun = noun_dict[word]
                    current_subject = current_noun  # 주어로 설정
                    connected_nouns = [current_noun]
                    current_adjectives = []
                    i += 2  # 조사도 스킵
                    continue
            
            # 일반 명사: 새로운 명사 객체 생성
            if word not in noun_dict:
                new_noun = imagine(word, current_adjectives.copy())
                noun_dict[word] = new_noun
                noun_order.append(word)
            else:
                noun_dict[word].adjectives.extend(current_adjectives)
            
            current_noun = noun_dict[word]
            connected_nouns.append(current_noun)
            current_adjectives = []
        
        # 동사 처리
        elif pos in ['VV', 'VXV']:  # 일반 동사와 보조동사 모두 처리
            all_verbs.append(word)
            
            # 동사 사전 확인
            if word in verb_patterns:
                pattern = verb_patterns[word]
                nouns_in_order = [noun_dict[n] for n in noun_order]
                
                # 패턴에 따라 명사 매핑
                subject = None
                indirect_object = None
                obj = None
                
                for idx, role in enumerate(pattern):
                    if idx < len(nouns_in_order):
                        if role == "subject":
                            subject = nouns_in_order[idx]
                        elif role == "indirect_object":
                            indirect_object = nouns_in_order[idx]
                        elif role == "object":
                            obj = nouns_in_order[idx]
                
                # 관계 생성
                if subject is not None and obj is not None:
                    relation = Relation(subject, word, obj, indirect_object=indirect_object)
                    relations.append(relation)
            else:
                # 사전에 없으면 기존 로직 사용
                subject_for_relation = current_subject
                if subject_for_relation is None and len(noun_order) > 0:
                    subject_for_relation = noun_dict[noun_order[0]]
                
                if subject_for_relation is not None and current_noun is not None:
                    if current_noun != subject_for_relation:
                        relation = Relation(subject_for_relation, word, current_noun, indirect_object=current_indirect_object)
                        relations.append(relation)
                        current_indirect_object = None
        
        # 종결어미 처리 - 절이 끝남
        elif pos.startswith('EF'):
            # 마지막 형용사를 현재 명사에 붙이기
            if current_adjectives and current_noun is not None:
                current_noun.adjectives.extend(current_adjectives)
                current_adjectives = []
            connected_nouns = []
            current_subject = None
            current_indirect_object = None
        
        i += 1
    
    # 루프 종료 후 남은 형용사가 있으면 현재 명사에 붙이기
    if current_adjectives and current_noun is not None:
        current_noun.adjectives.extend(current_adjectives)
    
    # 명사가 없으면 형용사, 동사, position 리스트 반환
    if not noun_dict:
        return {
            'adjectives': all_adjectives,
            'verbs': all_verbs,
            'position': all_positions
        }
    
    # 명사가 있으면 명사 객체 리스트와 관계 리스트 반환
    noun_objects = [noun_dict[noun] for noun in noun_order]
    return {
        'noun_objects': noun_objects,
        'relations': relations
    }


def extract_pos(text):
    """Kkma를 이용해서 형태소 분석 (분리 없음)"""
    # 전체 텍스트를 한 번에 분석
    text = text.strip()
    
    # 형태소 분석
    tokens = kkma.pos(text)
    
    return [tokens]

def extract_morphs(text):
    """Kkma로 형태소만 추출"""
    text = text.strip()
    
    # 형태소만 추출
    morphs = kkma.morphs(text)
    
    return [morphs]


def extract_nouns_only(text):
    """Kkma로 명사만 추출하여 문자열 리스트로 반환"""
    text = text.strip()
    
    # 형태소 분석
    tokens = kkma.pos(text)
    
    result_nouns = []
    for word, pos in tokens:
        if pos.startswith('N'):
            result_nouns.append(word)
    
    return result_nouns


def split_sentences_by_connective(text):
    """Kkma와 Okt를 사용하여 동사+연결어미나 접속사로 문장을 분리
    
    예: "개가 뛰고 고양이가 울었다" 
    → ["개가 뛰고", "고양이가 울었다"]
    """
    text = text.strip()
    
    # Kkma 형태소 분석으로 분리 지점 찾기
    kkma_tokens = kkma.pos(text)
    
    # 각 형태소의 누적 위치를 추적하며 분리 지점 찾기
    split_positions = []  # 분리할 위치들
    current_pos = 0
    
    for word, pos in kkma_tokens:
        # 텍스트에서 현재 형태소 찾기
        word_pos = text.find(word, current_pos)
        if word_pos != -1:
            current_pos = word_pos + len(word)
        
        # EC: 연결어미 또는 MAJ: 접속사일 때 분리 지점 기록
        if pos.startswith('EC') or pos == 'MAJ':
            split_positions.append(current_pos)
    
    # 분리 지점을 기반으로 문장 분리 (공백 유지)
    if not split_positions:
        return [text]
    
    sentences = []
    start = 0
    for pos in split_positions:
        sentences.append(text[start:pos])
        start = pos
    
    # 마지막 문장 추가
    if start < len(text):
        sentences.append(text[start:])
    
    return [s.strip() for s in sentences if s.strip()]


def load_qa_from_json(json_file):
    """JSON 파일에서 질문-답변 로드"""
    import json
    with open(json_file, 'r', encoding='utf-8') as f:
        return json.load(f)


if __name__ == "__main__":
    # JSON 파일에서 텍스트 로드
    
    conversations = load_qa_from_json("conversations.json")
    if(not conversations[0].get('text', "").startswith("Q")):    
        print("=" * 60)
        print("[ 명사, 형용사, 동사 추출 결과 ]")
        print("=" * 60)
        
        for i, conversation in enumerate(conversations, 1):
            text = conversation.get('text', "")
            print(f"\n원문 [{i}]: {text}")
            
            # 문장 분리
            split_sentences = split_sentences_by_connective(text)
            print(f"분리된 문장: {split_sentences}")
            
            # 각 문장에서 명사, 형용사, 동사 추출
            print("\n📝 추출 결과:")
            for j, sentence in enumerate(split_sentences, 1):
                result = extract_nouns_adjectives_verbs(sentence)
                print(f"  [{j}] {sentence}")
                if isinstance(result, dict):
                    if 'noun_objects' in result:  # 명사가 있는 경우
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
                    else:  # 명사가 없는 경우
                        print(f"      형용사: {result.get('adjectives', [])}")
                        print(f"      동사: {result.get('verbs', [])}")
                        print(f"      위치 정보: {result.get('position', [])}")
                
        
        print("\n" + "=" * 60)
    else:
        print("질문-답변 형식이 감지되었습니다. 질문과 답변을 분리하여 출력합니다.")
        seting = conversations[0:2]
        conversations = conversations[2:]
        seting_text = seting[1].get('text', "")
        result = extract_nouns_adjectives_verbs(seting_text[2:])
        
        # result가 dict이고 noun_objects가 있는 경우
        if isinstance(result, dict) and 'noun_objects' in result:
            nouns = result['noun_objects']
        else:
            nouns = []
        
        print(f"설정 명사 객체: {nouns}")
        current_noun = None
        
        for i, conversation in enumerate(conversations, 1):
            text = conversation.get('text', "")
            if(text.startswith("Q:")):
                current_noun = None
                text = text[2:].strip()
                # Q에서 명사만 추출
                extracted_items = extract_nouns_only(text)
                # 추출된 명사와 nouns에 겹치는 단어가 있는지 확인
                for noun_str in extracted_items:
                    for noun_obj in nouns:
                        if noun_str == noun_obj.noun:
                            current_noun = noun_obj
                            break
                    if current_noun is not None:
                        break
            elif(text.startswith("A:")):
                text = text[2:].strip()
                result = extract_nouns_adjectives_verbs(text)
                
                if current_noun is not None:
                    # A에서 명사와 형용사 추출
                    if isinstance(result, dict):
                        if 'noun_objects' in result:  # 명사가 있는 경우
                            extract_nouns_list = result['noun_objects']
                            for noun_obj in extract_nouns_list:
                                current_noun.adjectives.extend(noun_obj.adjectives)
                        else:  # 명사가 없고 형용사/동사만 있는 경우
                            adjectives = result.get('adjectives', [])
                            current_noun.adjectives.extend(adjectives)
        
        print("\n최종 명사 객체:")
        for noun_obj in nouns:
            noun_obj.print_all()


