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
positions = ["왼쪽", "오른쪽", "위", "아래", "앞", "뒤","전","후"]#상대적 위치 저장
class imagine:
    """명사와 그 수식어(형용사)를 저장하는 클래스"""
    def __init__(self, noun, adjectives=None):
        self.noun = noun  # 명사
        self.adjectives = adjectives if adjectives else []  # 명사의 수식어(형용사)
        self.position = []# (상대적 위치,물체)로 저장하기 위한 리스트
        self.actions = []  # 명사의 행동
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
        """명사, 형용사, 동사, 대상 모두 출력"""
        print(f"명사: {self.noun}")
        print(f"형용사: {self.adjectives}")
        print(f"동사: {self.actions}")
        if self.target:
            print(f"대상: {self.target}")
    
    def __repr__(self):
        return f"<{self.noun}: {self.adjectives} {self.actions}>"
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
    """한 문장에서 명사, 형용사, 동사를 추출하여 imagine 객체로 반환
    명사가 없으면 형용사와 동사의 리스트를 반환합니다.
    
    Args:
        sentence: 분석할 문장
    
    Returns:
        - 명사가 있으면: 명사 기준으로 organize된 imagine 객체 리스트
        - 명사가 없으면: {'adjectives': [형용사들], 'verbs': [동사들]}
    """
    sentence = sentence.strip()
    
    # 형태소 분석
    tokens = postprocess_kkma_pos(sentence)
    # 명사별로 정보 저장
    noun_objects = []  # imagine 객체 리스트
    current_adjectives = []
    current_noun = None
    connected_nouns = []  # 현재 절에서 접속사로 연결된 명사들
    
    # 명사가 없을 때를 위한 추적
    all_adjectives = []
    all_verbs = []
    
    i = 0
    while i < len(tokens):
        word, pos = tokens[i]
        
        # 형용사 수집
        if pos in ['VA'] or word.endswith("색"):
            current_adjectives.append(word)
            all_adjectives.append(word)  # 전체 형용사 추적
            # 형용사를 수집하면 바로 이전 명사에 붙이기
            if current_noun is not None:
                current_noun.adjectives.extend(current_adjectives)
                current_adjectives = []
        # 명사 처리 (색 명사는 이미 형용사로 수집되었으므로, 별도로 명사 처리)
        elif pos.startswith('N') and word not in positions:
            # 다음 토큰이 목적격 조사(을/를)인지 확인
            if i + 1 < len(tokens):
                next_word, next_pos = tokens[i + 1]
                if next_pos == 'JKO':  # 목적격 조사 (을/를)
                    # 이 명사를 현재 명사의 target으로 설정
                    if current_noun is not None:
                        current_noun.target = word
                    i += 2  # 조사도 스킵
                    continue
                elif next_pos == 'JKM':  # 접속 조사 (와, 과, 및 등)
                    # 새로운 명사 객체를 생성하고 현재 절의 명사 리스트에 추가
                    new_noun = imagine(word, current_adjectives.copy())
                    noun_objects.append(new_noun)
                    connected_nouns.append(new_noun)
                    current_noun = new_noun
                    current_adjectives = []
                    i += 2  # 접속사도 스킵
                    continue
            
            # 일반 명사: 새로운 명사 객체 생성
            new_noun = imagine(word, current_adjectives.copy())
            noun_objects.append(new_noun)
            connected_nouns.append(new_noun)  # 현재 절의 명사 리스트에 추가
            current_noun = new_noun
            current_adjectives = []
        # 동사 처리
        elif pos.startswith('VV'):
            all_verbs.append(word)  # 전체 동사 추적
            # 현재 절의 모든 명사에 동사 추가 (접속사로 연결된 명사들 포함)
            for noun_obj in connected_nouns:
                if word not in noun_obj.actions:
                    noun_obj.actions.append(word)
        # 종결어미 처리 - 절이 끝남
        elif pos.startswith('EF'):
            connected_nouns = []  # 새로운 절 시작
        
        i += 1
    
    # 명사가 없으면 형용사와 동사 리스트 반환
    if not noun_objects:
        return {
            'adjectives': all_adjectives,
            'verbs': all_verbs
        }
    
    return noun_objects


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
    
    conversations = load_qa_from_json("test.json")
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
                nouns = extract_nouns_adjectives_verbs(sentence)
                print(f"  [{j}] {sentence}")
                for noun_obj in nouns:
                    print(f"      명사: {noun_obj.noun}")
                    if noun_obj.adjectives:
                        print(f"      형용사: {noun_obj.adjectives}")
                    if noun_obj.actions:
                        print(f"      동사: {noun_obj.actions}")
                    if noun_obj.target:
                        print(f"      대상(Target): {noun_obj.target}")
        
        print("\n" + "=" * 60)
    else:
        print("질문-답변 형식이 감지되었습니다. 질문과 답변을 분리하여 출력합니다.")
        seting = conversations[0:2]
        conversations = conversations[2:]
        seting_text = seting[1].get('text', "")
        nouns = extract_nouns_adjectives_verbs(seting_text[2:])
        print(nouns)
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
                extract = extract_nouns_adjectives_verbs(text)
                if current_noun is not None:
                    # A에서 명사와 형용사 추출
                    if isinstance(extract, dict):
                        adjectives = extract.get('adjectives', [])
                        verbs = extract.get('verbs', [])
                        current_noun.adjectives.extend(adjectives)
                        current_noun.actions.extend(verbs)
                    elif isinstance(extract, list):  # 새로운 명사들이 추출된 경우
                        for noun_obj in extract:
                            if noun_obj.noun in [n.noun for n in nouns]:
                                for n in nouns:
                                    if noun_obj.noun == n.noun:
                                        n.adjectives.extend(noun_obj.adjectives)
                                        n.actions.extend(noun_obj.actions)
                                        break
                            else:
                                nouns.append(noun_obj)
                else:
                    if isinstance(extract, list):
                        for noun_obj in extract:
                            if(noun_obj.noun in [n.noun for n in nouns]):
                                for n in nouns:
                                    if noun_obj.noun == n.noun:
                                        n.adjectives.extend(noun_obj.adjectives)
                                        n.actions.extend(noun_obj.actions)
                                        break
                            else:
                                nouns.append(noun_obj)
        print(nouns)
# if __name__ == "__main__":
#         conversations = load_qa_from_json("conversations.json")
#         text = conversations[0].get('text', "")
#         texts = split_sentences_by_connective(text)
#         for text in texts:
#             tokens = extract_nouns_adjectives_verbs(text)
#             print(tokens)

