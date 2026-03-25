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
        return f"<{self.noun}: {self.adjectives}>"


def extract_nouns_adjectives_verbs(sentence):
    """한 문장에서 명사, 형용사, 동사를 추출하여 imagine 객체로 반환
    접속사로 연결된 명사들은 같은 동사를 공유합니다.
    
    Args:
        sentence: 분석할 문장
    
    Returns:
        명사 기준으로 organize된 imagine 객체 리스트
    """
    sentence = sentence.strip()
    
    # 형태소 분석
    tokens = kkma.pos(sentence)
    
    # 명사별로 정보 저장
    noun_objects = []  # imagine 객체 리스트
    current_adjectives = []
    current_noun = None
    connected_nouns = []  # 현재 절에서 접속사로 연결된 명사들
    
    i = 0
    while i < len(tokens):
        word, pos = tokens[i]
        
        # 형용사 수집
        if pos in ['VA', 'JJ'] or word.endswith("색"):
            current_adjectives.append(word)
        # 명사 처리
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
        elif pos.startswith('V'):
            # 현재 절의 모든 명사에 동사 추가 (접속사로 연결된 명사들 포함)
            for noun_obj in connected_nouns:
                if word not in noun_obj.actions:
                    noun_obj.actions.append(word)
        # 종결어미 처리 - 절이 끝남
        elif pos.startswith('EF'):
            connected_nouns = []  # 새로운 절 시작
        
        i += 1
    
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


def split_sentences_by_connective(text):
    """Kkma와 Okt를 사용하여 동사+연결어미나 접속사로 문장을 분리
    
    예: "개가 뛰고 고양이가 울었다" 
    → ["개가 뛰고", "고양이가 울었다"]
    """
    text = text.strip()
    
    # Kkma 형태소 분석으로 분리
    kkma_tokens = kkma.pos(text)
    
    sentences = []
    current_sentence = []
    
    for word, pos in kkma_tokens:
        current_sentence.append(word)
        
        # EC: 연결어미 (ECE, ECN 등 포함)
        # MAJ: 접속사
        if pos.startswith('EC') or pos == 'MAJ':
            sentences.append(''.join(current_sentence))
            current_sentence = []
    
    # 마지막 문장 추가
    if current_sentence:
        sentences.append(''.join(current_sentence))
    
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


