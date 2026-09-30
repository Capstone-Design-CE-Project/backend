import os
from konlpy.tag import Kkma
from konlpy.tag import Okt
import re
import warnings
import logging
import time
import sys
import contextlib
import io
from pathlib import Path

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

PREVERBAL_NEGATION_MARKERS = {"안", "못", "아니"}
POSTVERBAL_NEGATION_AUXILIARIES = {"않", "못하"}


def detect_polarity_bit(tokens, verb_index, clause_start=None):
    """술어의 긍정(1)/부정(0)을 한 비트로 계산한다.

    한 절 안에서 `안/못 + 동사`와 `동사+-지 않다/못하다`를 각각 하나의
    부정 연산자로 보고 발견할 때마다 ``not``을 적용한다. 따라서 이중
    부정은 다시 긍정(1)이 된다. 명사 `안`과 동사 어간 `안-`은 품사와
    위치가 다르므로 부정 연산자로 세지 않는다.
    """
    polarity_bit = 1
    detected_markers = []
    start = max(0, verb_index - 3) if clause_start is None else clause_start

    for index in range(start, verb_index):
        word, pos = tokens[index]
        if word in PREVERBAL_NEGATION_MARKERS and pos == "MAG":
            polarity_bit = int(not polarity_bit)
            detected_markers.append(word)

    # KKMA: 열지 않았다 -> 열/VV + ㄹ지/ECD + 않/VXV
    #       열지 못했다 -> 열/VV + ㄹ지/ECD + 못하/VX
    connector_index = verb_index + 1
    if connector_index < len(tokens):
        connector_word, connector_pos = tokens[connector_index]
        if connector_pos.startswith("EC") and connector_word.endswith("지"):
            auxiliary_index = connector_index + 1
            while (
                auxiliary_index < len(tokens)
                and tokens[auxiliary_index][1] == "JX"
            ):
                auxiliary_index += 1
            if auxiliary_index < len(tokens):
                auxiliary_word, auxiliary_pos = tokens[auxiliary_index]
                if (
                    auxiliary_word in POSTVERBAL_NEGATION_AUXILIARIES
                    and auxiliary_pos in {"VXV", "VXA", "VX", "VV"}
                ):
                    polarity_bit = int(not polarity_bit)
                    detected_markers.append(auxiliary_word)

    marker = detected_markers[-1] if polarity_bit == 0 else None
    if DEBUG_MORPHS and detected_markers:
        print(
            f"[DEBUG] 극성 계산: markers={detected_markers}, "
            f"polarity_bit={polarity_bit}, 동사 위치={verb_index}"
        )
    return polarity_bit, marker


def detect_negation(tokens, verb_index, clause_start=None):
    """기존 호출부와의 호환을 위해 최종 부정 표지만 반환한다."""
    _, marker = detect_polarity_bit(tokens, verb_index, clause_start)
    return marker

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

class RelativeClause:
    """중심 명사를 수식하는 관형절의 내부 관계."""

    def __init__(
        self,
        subject,
        verb,
        obj=None,
        head_role="content",
        negation=None,
        context_nouns=None,
        direct_objects=None,
        indirect_objects=None,
        object_complements=None,
        subjects=None,
        polarity_bit=1,
    ):
        self.subject = subject
        self.subjects = list(subjects or [subject])
        if subject not in self.subjects:
            self.subjects.append(subject)
        self.verb = verb
        self.obj = obj
        self.head_role = head_role
        self.polarity_bit = int(bool(polarity_bit))
        if negation is not None and self.polarity_bit == 1:
            self.polarity_bit = 0
        self.negation = negation if self.polarity_bit == 0 else None
        self.context_nouns = list(context_nouns or [])
        self.direct_objects = list(direct_objects or [])
        self.indirect_objects = list(indirect_objects or [])
        self.object_complements = list(object_complements or [])

    def signature(self):
        return (
            self.subject,
            tuple(self.subjects),
            self.verb,
            self.obj,
            self.head_role,
            self.polarity_bit,
            self.negation,
            tuple(self.context_nouns),
            tuple(self.direct_objects),
            tuple(self.indirect_objects),
            tuple(self.object_complements),
        )

    def _format(self):
        object_name = (
            ",".join(self.direct_objects)
            if len(self.direct_objects) > 1
            else (self.obj or "None")
        )
        negation = f"[{self.negation}]" if self.negation else ""
        subject_name = ",".join(self.subjects)
        indirect = (
            f" - {', '.join(self.indirect_objects)}에게"
            if self.indirect_objects
            else ""
        )
        complements = (
            f" | object_complement: {self.object_complements}"
            if self.object_complements
            else ""
        )
        context = (
            f" | context: {self.context_nouns}"
            if self.context_nouns
            else ""
        )
        return (
            f"({subject_name} {negation}{self.verb} {object_name})"
            f"{indirect}{complements}{context}"
        )

    def __repr__(self):
        return self._format()


class imagine:
    """명사와 그 수식어(형용사)를 저장하는 클래스"""
    def __init__(self, noun, adjectives=None):
        self.noun = noun  # 명사
        self.adjectives = adjectives if adjectives else []  # 명사의 수식어(형용사 원형)
        self.position = [] # 위치 정보: [(대상명사, 위치), ...] 형태로 저장 (예: [("등대", "옆"), ("바닷가", "앞")])
        self.moderators = []  
        self.target = None
        self.relative_clauses = []
        # EntityTree에서는 같은 imagine이 여러 턴에 다시 등장해도 각
        # 발생을 별도 노드로 보존한다.
        self.entity_mention_ids = []
    
    def add_adjective(self, adjective):
        self.adjectives.append(adjective)

    def add_relative_clause(self, relative_clause):
        signatures = {item.signature() for item in self.relative_clauses}
        if relative_clause.signature() not in signatures:
            self.relative_clauses.append(relative_clause)

    def format_for_relation(self):
        if not self.relative_clauses:
            return self.noun
        clauses = ", ".join(str(item) for item in self.relative_clauses)
        return f"{self.noun}{{relative_clause: [{clauses}]}}"
    
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
        print(f"관형절: {self.relative_clauses}")
    
    def __repr__(self):
        return (
            f"<{self.noun}: {self.adjectives} {self.position} "
            f"Moderators: {self.moderators} RelativeClauses: {self.relative_clauses}>"
        )


class EntityTreeNode:
    """동일 객체 판정을 하기 전의 분류/발생 노드."""

    def __init__(
        self,
        node_id,
        name,
        head,
        kind,
        parent_id=None,
        turn_index=None,
        source="answer",
        adjectives=None,
        resolved_to=None,
        object_ref=None,
    ):
        self.node_id = node_id
        self.name = name
        self.head = head
        self.kind = kind
        self.parent_id = parent_id
        self.turn_index = turn_index
        self.source = source
        self.adjectives = list(adjectives or [])
        self.children = []
        self.resolved_to = resolved_to
        # 기존 Relation/imagine 구조를 깨지 않고 동일 객체 판정 결과를
        # 반영하기 위한 런타임 참조다. 직렬화 결과에는 넣지 않는다.
        self.object_ref = object_ref

    def to_dict(self):
        return {
            "node_id": self.node_id,
            "name": self.name,
            "head": self.head,
            "kind": self.kind,
            "parent_id": self.parent_id,
            "turn_index": self.turn_index,
            "source": self.source,
            "adjectives": list(self.adjectives),
            "children": list(self.children),
            "resolved_to": self.resolved_to,
        }


class EntityTree:
    """중심 명사 아래에 모든 객체 발생을 누적하는 비병합 트리.

    같은 중심어를 가진 표현도 각각 고유한 mention ID로 추가한다. 동일
    객체임이 확실한 경우에도 노드를 합치거나 삭제하지 않고 ``resolved_to``
    만 기록한다. 따라서 `장식`과 `별 장식`의 발생 이력은 모두 남고,
    `방울이`와 `바둑이`도 `category="개"`를 주면 서로 다른 자식이 된다.
    """

    _PLURAL_EXCEPTIONS = {"아들", "그들", "이들", "저들", "우리들"}

    def __init__(self):
        self.nodes = {}
        self.concept_ids = {}
        self.mention_ids = []
        self._next_concept = 1
        self._next_mention = 1
        root = EntityTreeNode(
            "root",
            "entities",
            "entities",
            "root",
            parent_id=None,
            source="system",
        )
        self.nodes[root.node_id] = root

    @staticmethod
    def _compact(value):
        return re.sub(r"\s+", "", str(value or ""))

    def infer_head(self, name):
        name = str(name or "").strip()
        if not name:
            return "unknown"
        compact_name = self._compact(name)
        existing = [
            head
            for head in self.concept_ids
            if compact_name.endswith(self._compact(head))
        ]
        if existing:
            return max(existing, key=lambda item: len(self._compact(item)))

        head = name.split()[-1]
        if (
            head.endswith("들")
            and len(head) > 2
            and head not in self._PLURAL_EXCEPTIONS
        ):
            head = head[:-1]
        return head

    def _get_or_create_concept(self, head):
        compact_head = self._compact(head) or "unknown"
        concept_id = self.concept_ids.get(compact_head)
        if concept_id is not None:
            return self.nodes[concept_id]
        concept_id = f"c{self._next_concept:04d}"
        self._next_concept += 1
        concept = EntityTreeNode(
            concept_id,
            head,
            head,
            "concept",
            parent_id="root",
            source="system",
        )
        self.nodes[concept_id] = concept
        self.nodes["root"].children.append(concept_id)
        self.concept_ids[compact_head] = concept_id
        return concept

    def add_mention(
        self,
        name,
        *,
        category=None,
        turn_index=None,
        source="answer",
        adjectives=None,
        object_ref=None,
    ):
        """병합하지 않고 항상 새로운 객체 발생 노드를 추가한다."""
        name = str(name or "").strip()
        if not name:
            return None
        head = str(category or self.infer_head(name)).strip()
        concept = self._get_or_create_concept(head)
        mention_id = f"m{self._next_mention:06d}"
        self._next_mention += 1
        mention = EntityTreeNode(
            mention_id,
            name,
            head,
            "mention",
            parent_id=concept.node_id,
            turn_index=turn_index,
            source=source,
            adjectives=adjectives,
            object_ref=object_ref,
        )
        self.nodes[mention_id] = mention
        concept.children.append(mention_id)
        self.mention_ids.append(mention_id)
        return mention

    def canonical_id(self, node_id):
        """resolved_to 사슬을 따라가되 순환이 생기면 원래 노드를 유지한다."""
        current = node_id
        visited = set()
        while current in self.nodes:
            if current in visited:
                return node_id
            visited.add(current)
            target = self.nodes[current].resolved_to
            if target is None or target not in self.nodes:
                return current
            current = target
        return node_id

    def resolve_to(self, node, canonical):
        """노드를 삭제하지 않고 같은 객체를 가리키는 연결만 기록한다."""
        node_id = getattr(node, "node_id", node)
        canonical_id = getattr(canonical, "node_id", canonical)
        if (
            node_id not in self.nodes
            or canonical_id not in self.nodes
            or node_id == canonical_id
        ):
            return False
        self.nodes[node_id].resolved_to = canonical_id
        return True

    def resolve_turn_mentions(self, mentions):
        """같은 턴의 Q 일반명사와 A의 유일한 구체 명사구만 연결한다."""
        resolutions = []
        groups = {}
        for node in mentions or []:
            groups.setdefault(self._compact(node.head), []).append(node)

        for group in groups.values():
            question_nodes = [node for node in group if node.source == "question"]
            answer_nodes = [node for node in group if node.source == "answer"]
            if not question_nodes or not answer_nodes:
                continue

            head = self._compact(group[0].head)
            distinct_answer_names = {}
            for node in answer_nodes:
                distinct_answer_names.setdefault(self._compact(node.name), []).append(node)

            specific_names = [
                name
                for name in distinct_answer_names
                if name != head and (name.endswith(head) or head in name)
            ]
            # `우리 논`과 `이웃 논`처럼 둘 이상의 구체 후보가 있으면
            # 최근성으로 고르지 않고 동일 객체 판정을 보류한다.
            if len(specific_names) != 1:
                continue

            canonical_name = specific_names[0]
            canonical = distinct_answer_names[canonical_name][0]
            for node in group:
                compact_name = self._compact(node.name)
                if node.node_id == canonical.node_id:
                    continue
                if compact_name == head or compact_name == canonical_name:
                    if self.resolve_to(node, canonical):
                        resolutions.append((node.node_id, canonical.node_id))
        return resolutions

    def object_canonical_ids(self, noun_object):
        return {
            self.canonical_id(node_id)
            for node_id in getattr(noun_object, "entity_mention_ids", [])
            if node_id in self.nodes
        }

    def same_object(self, left, right):
        left_ids = self.object_canonical_ids(left)
        right_ids = self.object_canonical_ids(right)
        return bool(left_ids and right_ids and left_ids & right_ids)

    def canonical_object(self, noun_object):
        for node_id in reversed(getattr(noun_object, "entity_mention_ids", [])):
            canonical_id = self.canonical_id(node_id)
            node = self.nodes.get(canonical_id)
            if node is not None and node.object_ref is not None:
                return node.object_ref
        return noun_object

    def mentions(self, head=None):
        mentions = [self.nodes[node_id] for node_id in self.mention_ids]
        if head is None:
            return mentions
        compact_head = self._compact(head)
        return [
            node
            for node in mentions
            if self._compact(node.head) == compact_head
        ]

    def to_dict(self):
        return {
            "root_id": "root",
            "mention_count": len(self.mention_ids),
            "nodes": {
                node_id: node.to_dict()
                for node_id, node in self.nodes.items()
            },
        }


class Relation:
    """기존 S-V-O 구조를 유지하면서 세부 목적어 역할을 함께 저장한다."""
    def __init__(
        self,
        subject,
        verb,
        obj,
        indirect_object=None,
        negation=None,
        context_nouns=None,
        direct_objects=None,
        indirect_objects=None,
        object_complements=None,
        subjects=None,
        reason_clauses=None,
        polarity_bit=1,
    ):
        self.subject = subject  # imagine 객체 (주어)
        self.subjects = list(subjects or [subject])
        if all(item.noun != subject.noun for item in self.subjects):
            self.subjects.append(subject)
        self.verb = verb  # 동사 (원형)
        self.obj = obj  # imagine 객체 (목적어)
        self.direct_objects = list(direct_objects or ([] if obj is None else [obj]))
        if obj is not None and all(item.noun != obj.noun for item in self.direct_objects):
            self.direct_objects.append(obj)
        self.indirect_objects = list(
            indirect_objects
            or ([] if indirect_object is None else [indirect_object])
        )
        self.indirect_object = (
            indirect_object
            or (self.indirect_objects[0] if self.indirect_objects else None)
        )
        self.object_complements = list(object_complements or [])
        self.reason_clauses = list(reason_clauses or [])
        self.polarity_bit = int(bool(polarity_bit))
        if negation is not None and self.polarity_bit == 1:
            self.polarity_bit = 0
        self.negation = negation if self.polarity_bit == 0 else None
        self.context_nouns = context_nouns if context_nouns else []  # 동사 처리 시점 noun_stack 문맥
        # 다중 턴에서 같은 명사 객체에 새 관형절이 병합되더라도 과거 관계의
        # 출력이 뒤늦게 바뀌지 않도록 생성 시점의 표시값을 보존한다.
        self._subject_display = self.subject.format_for_relation()
        self._subject_displays = [
            item.format_for_relation() for item in self.subjects
        ]
        self._object_display = (
            self.obj.format_for_relation() if self.obj else "None"
        )
        self._direct_displays = [
            item.format_for_relation() for item in self.direct_objects
        ]
        self._indirect_displays = [
            item.format_for_relation() for item in self.indirect_objects
        ]
        if not self._indirect_displays and self.indirect_object is not None:
            self._indirect_displays = [
                self.indirect_object.format_for_relation()
            ]
        self._complement_displays = [
            item.format_for_relation() for item in self.object_complements
        ]

    def _format(self):
        subject_noun = ",".join(self._subject_displays)
        direct_names = self._direct_displays
        obj_noun = (
            ",".join(direct_names)
            if len(direct_names) > 1
            else self._object_display
        )
        indirect_names = self._indirect_displays
        indirect_str = (
            f" - {', '.join(indirect_names)}에게"
            if indirect_names
            else ""
        )
        negation_str = f"[{self.negation}]" if self.negation else ""
        complement_names = self._complement_displays
        complement_str = (
            f" | object_complement: {complement_names}"
            if complement_names
            else ""
        )
        if self.context_nouns and any(
            "{relative_clause:" in str(item)
            for item in self.context_nouns
        ):
            context_str = (
                f" | context: [{', '.join(str(item) for item in self.context_nouns)}]"
            )
        else:
            context_str = (
                f" | context: {self.context_nouns}"
                if self.context_nouns
                else ""
            )
        reason_str = (
            f" | reason: [{', '.join(str(item) for item in self.reason_clauses)}]"
            if self.reason_clauses
            else ""
        )
        return (
            f"({subject_noun} {negation_str}{self.verb} {obj_noun})"
            f"{indirect_str}{complement_str}{context_str}{reason_str}"
        )

    def __repr__(self):
        return self._format()
    
    def print_relation(self):
        print(self._format())


# 관계 생성은 명사의 등장 순서가 아니라 조사와 절 경계를 기준으로 수행한다.
# 기존 noun_stack은 명사 객체/위치 정보 수집용으로만 남겨 두고, S-V-O 판정에는
# 아래 규칙 기반 분석기를 사용한다.
SUBJECT_TOPIC_PARTICLES = {"은", "는"}
ADDITIVE_PARTICLES = {"도"}
ADVERBIAL_PARTICLES = {
    "에", "에서", "에게", "에게서", "한테", "한테서",
    "까지", "부터", "마다", "쯤", "와", "과", "랑", "이랑",
}
TEMPORAL_NOUNS = {
    "오늘", "어제", "내일", "아침", "점심", "저녁", "밤",
    "지금", "그때", "옛날", "예전", "시간", "동안", "때",
    "처음", "아직", "하루", "종일", "달", "번", "날", "틈",
}
NOMINALIZER_NOUNS = {"것", "거", "줄", "수"}
RELATIVE_CONTENT_NOUNS = NOMINALIZER_NOUNS | {
    "법", "방법", "일", "곳", "날", "때", "틈",
    "얼굴", "모습", "이야기", "마음", "정", "습관",
}
MEASURE_NOUNS = {"개", "명", "그릇", "잔", "장", "마리", "번", "하나", "모"}
# 모든 동사는 활용될 수 있으므로 활용 가능 여부는 별도로 저장하지 않는다.
# 다만 답변에서 목적어가 생략됐을 때 Q의 대상을 복원하려면 동사의 논항 구조가
# 필요하다. TRANSITIVE_VERBS 대신 의미역 프레임으로 목적을 명확히 표현한다.
VERB_ARGUMENT_FRAMES = {
    lemma: frozenset({"direct_object"})
    for lemma in {
        "좋아하다", "읽다", "하다", "잘하다", "모르다", "만나다",
        "기억하다", "생각하다", "먹다", "보다", "주다", "입다",
        "받다", "팔다", "사다", "사먹다", "싸다", "짓다", "깎다",
        "깎아주다", "챙기다", "챙겨주다", "해주다", "안아보다",
        "뽑다", "대다", "두다", "도와주다", "보고 싶다", "하고 싶다",
        "마시다", "준비하다", "공경하다", "심다", "베다", "묶다",
        "담다", "말리다", "펴다", "기르다", "돌보다", "돕다",
        "줍다", "주워오다", "싸가다", "되어주다", "안아주다",
        "들려주다", "붙이다", "찍다", "알려주다", "살펴보다",
        "배우다", "떠올리다", "주고받다", "차려입다", "맡다",
        "쌓다", "건너다", "걷다", "삼다", "임명하다", "정하다",
        "부르다", "만들다", "여기다", "간주하다", "선출하다",
        "지정하다", "싸주다", "만들어주다", "나눠주다",
        "고쳐주다", "들려드리다", "다루다", "빚다", "가르치다",
        "나누다", "나눠먹다", "외우다", "보관하다", "건네다",
        "데려오다", "닫다",
        # 연결된 앞 절에서 이미 명시된 대상을 계속 다루는 대표 동사.
        # 새 목적어가 없는 경우에만 기존 절 공유 규칙이 적용되며,
        # 새 주어·새 목적어 및 자동사 차단 조건은 그대로 유지한다.
        "놓다", "꽂다", "싣다", "올려놓다", "넣다", "전하다",
        # 홀드아웃에서 생략 목적어 또는 관형절 중심어가 실제 대상으로
        # 확인된 동사만 작은 의미역 예외로 추가한다.
        "감싸다", "모으다", "적다",
    }
}
DEFAULT_EXPERIENCER_VERBS = {
    "기억나다", "생각나다", "모르다", "좋아하다", "보고 싶다",
    "안아보다",
}
BARE_SUBJECT_VERBS = {
    "끝나다", "태어나다", "들다", "되다", "나다", "없다",
    "좋아지다", "조용해지다", "시끌시끌해지다", "북적북적하다",
}
PERSON_NOUNS = {
    "나", "저", "너", "우리", "친구", "친구들", "사람",
    "아이", "아이들", "학생", "선생", "선생님", "어르신",
    "배우자", "아주머니", "부모님", "친척", "친척들",
    "손주", "손주들", "자기", "사람들", "어른들", "형제들",
    "가족", "가족들", "아버지", "어머니", "할아버지", "할머니",
    "큰아버지", "큰고모", "고모", "동생", "형", "누나", "언니",
    "오빠", "아들", "딸", "사촌", "사촌들",
}
NON_OBJECT_NOUNS = {"조금"}
QUESTION_GENERIC_NOUNS = {
    "무엇", "뭐", "어디", "어느", "어떤", "누구", "몇",
    "것", "거", "점", "일", "때", "이유", "방법", "곳",
}
# 형태소 분석기가 일부 `-하다` 형용사를 명사+XSV로 태깅하는 경우만
# 동사 일반화의 예외로 둔다.
ADJECTIVE_HADA_ROOTS = {
    "건강", "다정", "따뜻", "허전", "고소", "고단", "든든",
    "친절", "필요", "부지런",
}
_OKT_HADA_ADJECTIVE_CACHE = {}
# 연결어미 뒤에서만 보조 의미를 갖는 용언과 허용되는 연결 형태.
# 일반동사+일반동사는 이 표로 합치지 않고 기존의 어휘적 복합동사 예외만 쓴다.
AUXILIARY_CONNECTIVE_RULES = {
    "싶": {"고"},
    "계시": {"고"},
    "있": {"고", "아", "어", "여"},
    "되": {"게"},
    "주": {"아", "어", "여"},
    "드리": {"아", "어", "여"},
    "보": {"아", "어", "여"},
    "두": {"아", "어", "여"},
    "놓": {"아", "어", "여"},
    "버리": {"아", "어", "여"},
    "내": {"아", "어", "여"},
    "가": {"아", "어", "여"},
    "오": {"아", "어", "여"},
    "지": {"아", "어", "여"},
}
INDIRECT_PARTICLES = {"에게", "한테", "께", "에게서", "한테서"}
OBJECT_COMPLEMENT_PARTICLES = {"로", "으로"}
# '-아/어 주다' 계열은 _is_beneficiary_verb()에서 형태로 판정한다.
# 접미 형태만으로 수혜 의미를 확인할 수 없는 동사만 예외로 유지한다.
BENEFICIARY_EXCEPTION_VERBS = {"보내다", "먹이다"}
OBJECT_COMPLEMENT_VERBS = {
    "뽑다", "삼다", "임명하다", "정하다", "부르다", "만들다",
    "여기다", "간주하다", "선출하다", "지정하다", "기억하다",
}
INFERRED_PERSON_NAMES = set()
NOUN_PHRASE_JOIN_OVERRIDES = {
    ("친", "구들"): "친구들",
    ("윷", "놀이"): "윷놀이",
    ("오", "남매"): "오남매",
    ("한", "여름"): "한여름",
}
NON_CONTENT_ADNOMINALS = {"어떤", "무슨", "어느", "몇", "그"}
REASON_CONNECTIVE_ENDINGS = {
    "아서", "어서", "여서", "라서",
    "니까", "으니까", "므로", "으므로", "길래", "기에",
}
TEMPORAL_SEQUENCE_CONNECTIVES = {"뒤", "다음", "후", "전"}
IMMEDIATE_ACTION_CONNECTIVES = {"고 나서", "고서", "자", "자마자"}
# 대조 연결 `-지만`은 같은 주체를 보장하지 않는다. 다만 뒤 절에
# 직접목적어가 생략된 경우에는 앞 절에 명시된 대상을 이어갈 수 있다.
CONTRASTIVE_OBJECT_CONNECTIVES = {"지만"}
GENERAL_SHAREABLE_CLAUSE_CONNECTIVES = {
    "고", "아", "어", "여", "며", "면서", "다가",
}
SHAREABLE_CLAUSE_CONNECTIVES = (
    GENERAL_SHAREABLE_CLAUSE_CONNECTIVES
    | REASON_CONNECTIVE_ENDINGS
    | IMMEDIATE_ACTION_CONNECTIVES
    | CONTRASTIVE_OBJECT_CONNECTIVES
)
OBJECT_SHAREABLE_CLAUSE_CONNECTIVES = {
    "고", "아", "어", "여",
} | REASON_CONNECTIVE_ENDINGS | IMMEDIATE_ACTION_CONNECTIVES | TEMPORAL_SEQUENCE_CONNECTIVES | CONTRASTIVE_OBJECT_CONNECTIVES
# 경로를 을/를로 받을 수는 있지만 앞 절의 임의 사물을 생략 목적어로
# 이어받으면 이미지 관계가 크게 달라지는 이동 동사다.
OBJECT_INHERITANCE_BLOCKED_VERBS = {"걷다", "건너다"}
# 관형 술어 앞에서 새 절의 시작을 확정할 수 있는 강한 경계다. 단순
# `-아/-어/-여`는 `가+아+가져오다`, `씻어 말리다`처럼 합성·연쇄 술어와
# 구별하기 어려우므로 여기서는 제외하고 기존 역할 공유에만 사용한다.
ADNOMINAL_CLAUSE_BOUNDARY_CONNECTIVES = (
    REASON_CONNECTIVE_ENDINGS
    | TEMPORAL_SEQUENCE_CONNECTIVES
    | IMMEDIATE_ACTION_CONNECTIVES
    | CONTRASTIVE_OBJECT_CONNECTIVES
    | {"고", "며", "면서", "다가"}
)
ENABLE_ADNOMINAL_CONNECTOR_BOUNDARY = (
    os.environ.get("PROMPTMAKER_ADNOMINAL_CONNECTOR_BOUNDARY", "1") != "0"
)
ENABLE_CONNECTED_CLAUSE_SHARING = True
# NNG+NNG로 숨은 조사를 관계 생성 전에 복원하는 실험 스위치.
ENABLE_PRE_RELATION_NOUN_PARTICLE_REPAIR = True
# `먼지 묻은 책`처럼 관형 술어 바로 앞의 무조사 명사를 KKMA가
# 형용사+연결어미로 오인한 경우, Okt가 명사 어절로 확인할 때만 복원한다.
ENABLE_BARE_NOUN_ADNOMINAL_REPAIR = (
    os.environ.get("PROMPTMAKER_BARE_NOUN_ADNOMINAL_REPAIR", "1") != "0"
)
# 은/는/도를 조사만으로 주어·목적어에 고정하지 않는다. 명시 격조사,
# 시간·위치, 현재 Q와 역할이 확인된 중심어만 근거로 사용한다.
# 환경 변수는 같은 코드로 변경 전·후 일반화 검사를 하기 위한 스위치다.
ENABLE_ROLE_NEUTRAL_AUXILIARY_PARTICLES = (
    os.environ.get("PROMPTMAKER_ROLE_NEUTRAL_AUX", "1") != "0"
)
ENABLE_ADNOMINAL_HEAD_SCOPE_GUARD = (
    os.environ.get("PROMPTMAKER_ADNOMINAL_HEAD_SCOPE_GUARD", "1") != "0"
)
# Q의 대응 관형형에 명시 주어가 없으면 A에서 새로 나타난 문두 주어를
# 관형절 안으로 자동 편입하지 않는 임시 일반화 실험 스위치다.
ENABLE_QUESTION_SUBJECTLESS_ADNOMINAL_MAIN_GUARD = (
    os.environ.get("PROMPTMAKER_Q_SUBJECTLESS_ADNOMINAL_GUARD", "0") != "0"
)
# 관형절·본절에 걸친 주어 후보를 먼저 수집하고, 확정된 관형절 주어를
# 제외한 가장 앞선 후보를 본절에 남기는 임시 후보 배정 실험이다.
ENABLE_DEFERRED_SUBJECT_CANDIDATE_ALLOCATION = (
    os.environ.get("PROMPTMAKER_DEFERRED_SUBJECT_ALLOCATION", "0") != "0"
)
# Q/A에서 주어 없는 동일 관형 술어+중심 명사를 하나의 head modifier로
# 보존하고, A에서 새로 나온 앞 주어 후보는 본절에 남기는 실험이다.
ENABLE_QA_COMMON_ADNOMINAL_MODIFIER = (
    os.environ.get("PROMPTMAKER_QA_COMMON_ADNOMINAL_MODIFIER", "0") != "0"
)
REASON_RESULT_STEMS = {
    "고맙", "감사하", "미안하", "기쁘", "슬프", "행복하",
    "다행이", "힘들", "좋", "싫", "아쉽", "반갑",
}
REASON_TARGET_PREDICATES = {
    "말하다", "말하고 싶다", "말씀드리다", "말씀드리고 싶다",
    "전하다", "전하고 싶다", "이야기하다", "이야기하고 싶다",
}


def _is_noun_token(pos):
    return pos.startswith('N')


def _is_noun_phrase_modifier(tokens, index):
    """이미지 대상의 일부로 보존할 수 관형사·일반 관형사를 판정한다."""
    word, pos = tokens[index]
    if pos not in {"MDN", "MDT"} or word in NON_CONTENT_ADNOMINALS:
        return False

    # KKMA가 '바삭한 전', '꼼꼼한 사람'의 관형형 어미를
    # 바삭/MAG+한/MDN처럼 잘못 자른 경우 수량 '한'으로 합치지 않는다.
    if word == "한" and index > 0 and tokens[index - 1][1] == "MAG":
        return False
    return True


def _get_relation_noun(noun_dict, noun_name):
    """관계에 필요한 명사 객체를 기존 객체와 공유한다."""
    if noun_name not in noun_dict:
        noun_dict[noun_name] = imagine(noun_name)
    return noun_dict[noun_name]


def _noun_phrase_left_edge(tokens, index):
    """현재 명사와 연속된 명사·관형사 명사구의 시작 위치를 찾는다."""
    left = index
    while left > 0:
        previous_word, previous_pos = tokens[left - 1]
        if _is_noun_token(previous_pos):
            # NR 전체를 붙이면 '열여덟 살 때' 같은 일반 수량까지 서수처럼
            # 취급된다. 명사를 직접 꾸미는 첫째·둘째 계열만 포함한다.
            if previous_pos == "NR" and not previous_word.endswith("째"):
                break
            # '열여덟 살 때 아버지', '처음 기차'처럼 시간 표현 뒤에서
            # 새로운 참여자·대상이 시작되면 하나의 명사구로 합치지 않는다.
            if previous_word in TEMPORAL_NOUNS | {"오래전", "무렵"}:
                break
            left -= 1
            continue
        if _is_noun_phrase_modifier(tokens, left - 1):
            left -= 1
            continue
        break
    return left


def _noun_name_at(tokens, index):
    """조사 앞에서 연속된 명사들을 하나의 명사구로 반환한다."""
    left = _noun_phrase_left_edge(tokens, index)
    words = tuple(tokens[item][0] for item in range(left, index + 1))
    if words in NOUN_PHRASE_JOIN_OVERRIDES:
        return NOUN_PHRASE_JOIN_OVERRIDES[words]
    if len(words) > 1:
        return " ".join(words)
    return words[0]


def _noun_span_indices(tokens, index):
    """하나의 의미상 연속 명사구를 이루는 형태소 인덱스를 반환한다."""
    left = _noun_phrase_left_edge(tokens, index)
    return set(range(left, index + 1))


def _noun_phrase_right_edge(tokens, index):
    """현재 명사에서 이어지는 동일 명사구의 마지막 명사 위치를 찾는다."""
    right = index
    while right + 1 < len(tokens):
        if _is_noun_token(tokens[right + 1][1]):
            right += 1
            continue
        if (
            _is_noun_phrase_modifier(tokens, right + 1)
            and right + 2 < len(tokens)
            and _is_noun_token(tokens[right + 2][1])
        ):
            right += 2
            continue
        break
    return right


def _span_has_attached_particle_fragment(sentence, tokens, left, right):
    """'손/주가'처럼 붙여 쓴 명사+조사를 명사 연속으로 오인한 범위인지 확인한다."""
    particles = {"이", "가", "은", "는", "을", "를", "도"}
    for index in range(left + 1, right + 1):
        previous_word, previous_pos = tokens[index - 1]
        word, pos = tokens[index]
        if not (_is_noun_token(previous_pos) and _is_noun_token(pos)):
            continue
        for particle in particles:
            if len(word) <= len(particle) or not word.endswith(particle):
                continue
            surface = previous_word + word
            if re.search(
                rf"(?<![가-힣]){re.escape(surface)}(?![가-힣])",
                sentence,
            ):
                return True
    return False


def _register_noun_phrase_objects(sentence, tokens, noun_dict):
    """관계 생성 전에 최대 명사구 범위별 imagine 객체를 미리 등록한다."""
    registered = []
    for index, (_, pos) in enumerate(tokens):
        if not _is_noun_token(pos):
            continue
        if _noun_phrase_right_edge(tokens, index) != index:
            continue

        left = _noun_phrase_left_edge(tokens, index)
        if left == index:
            continue
        if (
            index + 1 < len(tokens)
            and tokens[index + 1][1] in {"XSA", "XSV"}
        ):
            continue
        if (
            left > 0
            and tokens[left - 1][0] == "몇"
            and tokens[left][1] in {"NNB", "NNM"}
        ):
            continue
        if _span_has_attached_particle_fragment(sentence, tokens, left, index):
            continue

        noun_name = _noun_name_at(tokens, index)
        if noun_name not in noun_dict:
            noun_dict[noun_name] = imagine(noun_name)
            registered.append(noun_name)
    return registered


def _merge_imagine_metadata(target, source):
    """정리 과정에서 합쳐지는 명사 객체의 의미 정보를 보존한다."""
    for adjective in source.adjectives:
        if adjective not in target.adjectives:
            target.adjectives.append(adjective)
    for position_info in source.position:
        if position_info not in target.position:
            target.position.append(position_info)
    for moderator in source.moderators:
        if moderator not in target.moderators:
            target.moderators.append(moderator)
    if target.target is None and source.target is not None:
        target.target = source.target
    for relative_clause in source.relative_clauses:
        target.add_relative_clause(relative_clause)


def _refresh_relation_displays(relation):
    """Relation의 객체를 교체한 뒤 생성 시점 표시값도 함께 갱신한다."""
    relation._subject_display = relation.subject.format_for_relation()
    relation._subject_displays = [
        item.format_for_relation() for item in relation.subjects
    ]
    relation._object_display = (
        relation.obj.format_for_relation() if relation.obj else "None"
    )
    relation._direct_displays = [
        item.format_for_relation() for item in relation.direct_objects
    ]


def _attach_quantity_targets(tokens, noun_dict, relations, adjective_bindings):
    """분리된 개수 표현을 관계가 선택한 직접목적어에 결합한다.

    ``국밥을 한 그릇 먹다``처럼 목적어 조사 뒤에 수량구가 따로 놓인
    경우만 다룬다. 대상 탐색은 관계 생성과 마찬가지로 가장 가까운 JKO
    명사구를 사용하고, 실제 Relation의 직접목적어와 일치할 때만 병합한다.
    ``한 번`` 같은 시간·횟수와 이미 완성된 ``생선 한 마리``는 제외한다.
    """
    redirects = {}

    for measure_index, (measure_word, measure_pos) in enumerate(tokens):
        if (
            not _is_noun_token(measure_pos)
            or measure_word not in MEASURE_NOUNS
            or measure_word in TEMPORAL_NOUNS
        ):
            continue

        quantity_left = _noun_phrase_left_edge(tokens, measure_index)
        quantity_name = _noun_name_at(tokens, measure_index)

        # '생선 한 마리'처럼 대상이 같은 명사구 안에 이미 들어 있으면
        # 선등록 단계에서 완성된 객체가 만들어졌으므로 다시 합치지 않는다.
        if any(
            _is_noun_token(tokens[index][1])
            for index in range(quantity_left, measure_index)
        ):
            continue

        target_name = None
        for index in range(quantity_left - 1, -1, -1):
            _, pos = tokens[index]
            if pos == "SF" or pos in {"VV", "VXV", "XSV"}:
                break
            if not _is_noun_token(pos):
                continue
            noun_span = _noun_span_indices(tokens, index)
            particle_index = max(noun_span) + 1
            if (
                particle_index < quantity_left
                and tokens[particle_index][1] == "JKO"
            ):
                target_name = _noun_name_at(tokens, index)
                break

        if target_name is None:
            continue

        # 형태만 보고 합치지 않고, 관계 분석에서도 같은 명사를 실제
        # 직접목적어로 채택한 경우에만 수량 대상을 확정한다.
        target_relation = None
        target_object = None
        for relation in reversed(relations):
            for direct_object in relation.direct_objects:
                if direct_object.noun == target_name:
                    target_relation = relation
                    target_object = direct_object
                    break
            if target_relation is not None:
                break
        if target_relation is None:
            continue

        combined_name = f"{target_name} {quantity_name}"
        if combined_name in noun_dict:
            combined_object = noun_dict[combined_name]
        else:
            combined_object = imagine(combined_name)
            noun_dict[combined_name] = combined_object

        _merge_imagine_metadata(combined_object, target_object)
        quantity_object = noun_dict.get(quantity_name)
        if quantity_object is not None:
            _merge_imagine_metadata(combined_object, quantity_object)

        target_relation.direct_objects = [
            combined_object if item.noun == target_name else item
            for item in target_relation.direct_objects
        ]
        if target_relation.obj is not None and target_relation.obj.noun == target_name:
            target_relation.obj = combined_object
        _refresh_relation_displays(target_relation)

        redirects[target_name] = combined_name
        redirects[quantity_name] = combined_name

    if not redirects:
        return adjective_bindings

    referenced_names = _relation_referenced_noun_names(relations)
    for old_name, new_name in redirects.items():
        if old_name == new_name or old_name in referenced_names:
            continue
        old_object = noun_dict.get(old_name)
        new_object = noun_dict.get(new_name)
        if old_object is None or new_object is None:
            continue
        _merge_imagine_metadata(new_object, old_object)
        del noun_dict[old_name]

    cleaned_bindings = []
    seen_bindings = set()
    for noun_name, adjective in adjective_bindings:
        noun_name = redirects.get(noun_name, noun_name)
        binding = (noun_name, adjective)
        if binding in seen_bindings:
            continue
        seen_bindings.add(binding)
        cleaned_bindings.append(binding)
    return cleaned_bindings


def _relation_referenced_noun_names(relations):
    """Relation 및 중첩 관형절에서 실제로 쓰이는 명사 이름을 모은다."""
    names = set()
    visited_nouns = set()
    visited_relations = set()

    def visit_noun(noun):
        if noun is None or not hasattr(noun, "noun"):
            return
        object_id = id(noun)
        if object_id in visited_nouns:
            return
        visited_nouns.add(object_id)
        names.add(noun.noun)
        for relative_clause in noun.relative_clauses:
            visit_relation(relative_clause)

    def visit_relation(relation):
        relation_id = id(relation)
        if relation_id in visited_relations:
            return
        visited_relations.add(relation_id)
        for noun in relation.subjects:
            visit_noun(noun)
        for noun in relation.direct_objects:
            visit_noun(noun)
        for noun in relation.indirect_objects:
            visit_noun(noun)
        for noun in relation.object_complements:
            visit_noun(noun)
        for noun in relation.context_nouns:
            visit_noun(noun)
        for reason_clause in getattr(relation, "reason_clauses", []):
            visit_relation(reason_clause)

    for relation in relations:
        visit_relation(relation)
    return names


def _position_is_nominal_use(tokens, location):
    """'전을 준비하다'처럼 위치어와 동형인 일반 명사 사용인지 판정한다."""
    for index, (word, _) in enumerate(tokens):
        if word != location:
            continue
        next_pos = tokens[index + 1][1] if index + 1 < len(tokens) else None
        previous_pos = tokens[index - 1][1] if index > 0 else None
        if next_pos in {"JKO", "JKC"} or previous_pos == "JC":
            return True
    return False


def _cleanup_imagine_objects(
    sentence,
    tokens,
    noun_dict,
    relations,
    adjective_bindings,
):
    """관계 생성 후 내부 명사 객체의 분석 부산물과 중복을 정리한다.

    Relation은 생성 시점의 객체와 표시값을 유지한다. 이 단계는 외부에 반환할
    imagine 목록만 정돈하므로 기존 S-V-O 결과에는 영향을 주지 않는다.
    """
    redirects = {}
    referenced_names = _relation_referenced_noun_names(relations)

    # 음식 '전'처럼 위치어와 동형인 목적어는 위치 정보에서 제거한다.
    for noun_obj in noun_dict.values():
        noun_obj.position = [
            position_info
            for position_info in noun_obj.position
            if not (
                isinstance(position_info, tuple)
                and len(position_info) == 2
                and _position_is_nominal_use(tokens, position_info[1])
            )
        ]

    # '산도/산'처럼 형용사 처리에서만 보조사가 제거되어 생긴 별칭을 병합한다.
    for noun_name in list(noun_dict):
        if len(noun_name) <= 1:
            continue
        for particle in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES | {"만"}:
            if not noun_name.endswith(particle):
                continue
            base_name = noun_name[:-len(particle)]
            if base_name not in noun_dict or noun_name in referenced_names:
                continue
            _merge_imagine_metadata(noun_dict[base_name], noun_dict[noun_name])
            redirects[noun_name] = base_name
            del noun_dict[noun_name]
            break

    # KKMA가 '손주가'를 손/주가로 자른 경우처럼 원문에서 붙어 있는
    # 명사+조사 조각을 기존 대화 대상 또는 복원한 명사 하나로 합친다.
    for index in range(1, len(tokens)):
        previous_word, previous_pos = tokens[index - 1]
        word, pos = tokens[index]
        if not (_is_noun_token(previous_pos) and _is_noun_token(pos)):
            continue
        for particle in {"이", "가", "은", "는", "을", "를", "도"}:
            if len(word) <= len(particle) or not word.endswith(particle):
                continue
            reconstructed = previous_word + word[:-len(particle)]
            if not re.search(
                rf"(?<![가-힣]){re.escape(reconstructed + particle)}(?![가-힣])",
                sentence,
            ):
                continue
            plural_name = reconstructed + "들"
            if plural_name in noun_dict:
                target_name = plural_name
            elif reconstructed in noun_dict:
                target_name = reconstructed
            else:
                noun_dict[reconstructed] = imagine(reconstructed)
                target_name = reconstructed
            target = noun_dict[target_name]
            for fragment in (previous_word, word):
                if fragment not in noun_dict or fragment in referenced_names:
                    continue
                _merge_imagine_metadata(target, noun_dict[fragment])
                redirects[fragment] = target_name
                del noun_dict[fragment]
            break

    # 하/XSV·하/XSA 앞의 명사형 어근은 동사·형용사 분석 부산물이다.
    derivational_roots = {
        word
        for index, (word, pos) in enumerate(tokens[:-1])
        if pos.startswith("N")
        and tokens[index + 1][1] in {"XSA", "XSV"}
    }
    for root in derivational_roots:
        if root not in noun_dict or root in referenced_names:
            continue
        noun_obj = noun_dict[root]
        if noun_obj.position or noun_obj.relative_clauses or noun_obj.adjectives:
            continue
        redirects[root] = None
        del noun_dict[root]

    # 합성 명사 객체가 있으면 관계에 별도로 쓰이지 않은 구성요소를 병합한다.
    component_phrases = {}
    for phrase_name in noun_dict:
        if " " not in phrase_name:
            continue
        for component in set(phrase_name.split()):
            component_phrases.setdefault(component, []).append(phrase_name)

    for component, phrase_names in component_phrases.items():
        if component not in noun_dict or component in referenced_names:
            continue
        if len(phrase_names) != 1:
            component_obj = noun_dict[component]
            if not (
                component_obj.adjectives
                or component_obj.position
                or component_obj.moderators
                or component_obj.target is not None
                or component_obj.relative_clauses
            ):
                redirects[component] = None
                del noun_dict[component]
            continue
        phrase_name = phrase_names[0]
        if phrase_name not in noun_dict:
            continue
        _merge_imagine_metadata(noun_dict[phrase_name], noun_dict[component])
        redirects[component] = phrase_name
        del noun_dict[component]

    cleaned_bindings = []
    seen_bindings = set()
    for noun_name, adjective in adjective_bindings:
        while noun_name in redirects and redirects[noun_name] is not None:
            noun_name = redirects[noun_name]
        if redirects.get(noun_name, noun_name) is None:
            continue
        binding = (noun_name, adjective)
        if binding in seen_bindings:
            continue
        seen_bindings.add(binding)
        cleaned_bindings.append(binding)

    return cleaned_bindings


def _is_likely_person(noun_name):
    parts = noun_name.split()
    last = parts[-1]
    return (
        noun_name in PERSON_NOUNS
        or noun_name in INFERRED_PERSON_NAMES
        or last in PERSON_NOUNS
        or last in INFERRED_PERSON_NAMES
        or last in {"선배", "직원", "직원들", "동료", "동료들"}
        or noun_name.endswith("친구")
        or noun_name.endswith("사람")
        or noun_name.endswith("님")
        or noun_name.endswith(" 씨")
        or (parts[0] == "친구" and len(parts) > 1)
    )


def _infer_additive_role(tokens, noun_index):
    """`도`가 붙은 명사가 앞에서 명시된 가장 가까운 격 역할을 잇게 한다.

    `깨와 콩을 넣고 설탕도 넣다`, `윷놀이를 하고 제기도 차다`처럼
    동사가 달라지거나 동사 프레임에 목적어 정보가 없어도 `도`는 앞 명사의
    역할을 유지한다. 문장 경계를 넘지 않으며, 앞선 `도`는 확정 근거가
    아니므로 그보다 앞의 명시적 조사를 계속 찾는다.
    """
    if (
        noun_index + 1 >= len(tokens)
        or tokens[noun_index + 1][1] != "JX"
        or tokens[noun_index + 1][0] not in ADDITIVE_PARTICLES
    ):
        return None

    for previous_index in range(noun_index - 1, -1, -1):
        _, previous_pos = tokens[previous_index]
        if previous_pos == "SF":
            break
        if not _is_noun_token(previous_pos):
            continue
        if previous_index + 1 >= noun_index:
            continue

        particle_word, particle_pos = tokens[previous_index + 1]
        previous_name = _noun_name_at(tokens, previous_index)
        if particle_pos == "JKO":
            return "direct_object"
        if particle_pos == "JKS":
            return "subject"
        if particle_pos == "JX":
            if particle_word in ADDITIVE_PARTICLES:
                continue
            if particle_word in SUBJECT_TOPIC_PARTICLES:
                if ENABLE_ROLE_NEUTRAL_AUXILIARY_PARTICLES:
                    # 앞의 은/는도 아직 역할이 정해지지 않은 보조사다.
                    # 이를 주어로 고정해 뒤의 도까지 잘못 전파하지 않는다.
                    return None
                return (
                    "context"
                    if previous_name in TEMPORAL_NOUNS
                    else "subject"
                )
            if particle_word in ADVERBIAL_PARTICLES:
                return "context"
        if particle_pos == "JKM":
            if particle_word in INDIRECT_PARTICLES:
                return "indirect_object"
            if particle_word in OBJECT_COMPLEMENT_PARTICLES:
                return "object_complement"
            return "context"

    return None


def _recent_same_predicate_additive_role(
    relations,
    verb,
    noun_name,
    candidate_is_person=None,
):
    """이전의 같은 술어 관계에서 `도`가 이어 받을 역할을 찾는다.

    주어와 직접목적어가 모두 있는 관계에서는 사람/비인물이라는 매우
    거친 유형까지 같은 쪽만 선택한다. 두 역할이 모두 같은 유형이면
    `민수도 도왔다`처럼 실제로 모호하므로 확정하지 않는다. 장소와
    수혜자는 `에도`, `에게도`처럼 원래 격조사가 남은 경우에만 처리한다.
    """
    if candidate_is_person is None:
        candidate_is_person = _is_likely_person(noun_name)
    pending = list(relations or [])

    while pending:
        relation = pending.pop()
        pending.extend(getattr(relation, "reason_clauses", []))
        if relation.verb != verb:
            continue

        role_names = {
            "subject": [
                item.noun
                for item in getattr(relation, "subjects", [])
                if item.noun not in {"나", "저", "우리"}
            ],
            "direct_object": [
                item.noun
                for item in getattr(relation, "direct_objects", [])
            ],
        }
        matching_roles = [
            role
            for role, names in role_names.items()
            if any(
                _is_likely_person(name) == candidate_is_person
                for name in names
            )
        ]
        if len(matching_roles) == 1:
            return matching_roles[0]
        # 가장 최근의 같은 술어가 모호하면 더 오래된 관계를 섞지 않는다.
        return None

    return None


def _replace_hangul_vowel(syllable, vowel_index):
    """받침 없는 한글 음절의 중성을 교체한다."""
    code = ord(syllable) - 0xAC00
    if not (0 <= code < 11172):
        return syllable
    initial_index = code // 588
    final_index = code % 28
    if final_index != 0:
        return syllable
    return chr(0xAC00 + initial_index * 588 + vowel_index * 28)


def _conjugate_connective_stem(stem, connective):
    """보조용언 결합에 필요한 아/어 축약형을 만든다."""
    if not stem:
        return stem
    if connective == "여":
        if stem.endswith("하"):
            return stem[:-1] + "해"
        return stem + connective
    if connective not in {"아", "어"}:
        return stem + connective
    if stem.endswith("하"):
        return stem[:-1] + "해"

    last = stem[-1]
    code = ord(last) - 0xAC00
    if not (0 <= code < 11172):
        return stem + connective
    vowel_index = (code % 588) // 28
    final_index = code % 28
    if final_index != 0:
        return stem + connective

    if connective == "아":
        if vowel_index == 0:  # ㅏ + 아
            return stem
        if vowel_index == 8:  # ㅗ + 아 -> ㅘ
            return stem[:-1] + _replace_hangul_vowel(last, 9)
        if vowel_index == 18:  # ㅡ + 아
            return stem[:-1] + _replace_hangul_vowel(last, 0)
        return stem + connective

    if vowel_index in {1, 4, 5, 6, 7}:  # ㅐ/ㅓ/ㅔ/ㅕ/ㅖ + 어
        return stem
    if vowel_index == 20:  # ㅣ + 어 -> ㅕ
        return stem[:-1] + _replace_hangul_vowel(last, 6)
    if vowel_index == 13:  # ㅜ + 어 -> ㅝ
        return stem[:-1] + _replace_hangul_vowel(last, 14)
    if vowel_index == 18:  # ㅡ + 어
        return stem[:-1] + _replace_hangul_vowel(last, 4)
    return stem + connective


def _predicate_stem_for_auxiliary(tokens, predicate_index, compound_override=None):
    """보조용언 앞 술어를 결합 가능한 어간으로 복원한다."""
    if compound_override is not None:
        return (
            compound_override[:-1]
            if compound_override.endswith("다")
            else compound_override
        )

    word, pos = tokens[predicate_index]
    if (
        pos in {"XSV", "XSA"}
        and predicate_index > 0
        and tokens[predicate_index - 1][1] in {"NNG", "NNP", "XR"}
    ):
        return tokens[predicate_index - 1][0] + "하"
    base = get_base_form(word, 'VV')
    return base[:-1] if base.endswith("다") else base


def _compose_auxiliary_predicate(
    tokens,
    predicate_index,
    connective,
    auxiliary,
    compound_override=None,
):
    """앞 술어와 연결어미·보조용언을 관계 출력용 한 술어로 합친다."""
    stem = _predicate_stem_for_auxiliary(
        tokens,
        predicate_index,
        compound_override=compound_override,
    )
    if connective == "고":
        return f"{stem}고 {auxiliary}다"
    if connective == "게":
        return f"{stem}게 {auxiliary}다"
    conjugated = _conjugate_connective_stem(stem, connective)
    return f"{conjugated}{auxiliary}다"


def _has_explicit_direct_object(tokens, start, end):
    """술어 앞 구간에 을/를(JKO)로 확인되는 목적어가 있는지 반환한다."""
    for index in range(start, end - 1):
        if _is_noun_token(tokens[index][1]) and tokens[index + 1][1] == "JKO":
            return True
    return False


def _find_unresolved_topic_object(
    tokens,
    start,
    end,
    base_verb,
    excluded_indices=None,
):
    """미사용 사물 주제 `은/는`을 생략 목적어 후보로 반환한다."""
    if not _verb_accepts_inferred_direct_object(base_verb):
        return None, None, set()

    excluded_indices = set(excluded_indices or [])
    for index in range(end - 1, start - 1, -1):
        if (
            index in excluded_indices
            or not _is_noun_token(tokens[index][1])
            or index + 1 >= end
            or tokens[index + 1][1] != "JX"
            or tokens[index + 1][0] not in SUBJECT_TOPIC_PARTICLES
        ):
            continue

        noun_name = _noun_name_at(tokens, index)
        if (
            noun_name in TEMPORAL_NOUNS
            or noun_name in QUESTION_GENERIC_NOUNS
            or _is_likely_person(noun_name)
        ):
            continue

        # 주제 뒤에 을/를 목적어가 따로 있으면 주제는 주어로 유지한다.
        if _has_explicit_direct_object(tokens, index + 2, end):
            continue
        return noun_name, index, _noun_span_indices(tokens, index)

    return None, None, set()


def _question_topic_prefers_object(
    tokens,
    noun_index,
    noun_name,
    predicate_index,
    base_verb,
    default_object,
    default_object_predicates,
    question_required_roles=None,
):
    """Q의 목적어와 같은 `은/는` 명사를 본절 목적어로 낮춘다.

    주격조사, 명시적 을/를, 형용사 서술은 건드리지 않는다. 또한 현재
    술어가 Q에서 그 목적어와 연결된 술어일 때만 적용하여 이전 대화의
    중심어가 무관한 문장에 침투하지 않게 한다.
    """
    if (
        noun_index is None
        or not noun_name
        or default_object is None
        or noun_name != default_object.noun
        or "subject" in set(question_required_roles or [])
    ):
        return False

    noun_span = _noun_span_indices(tokens, noun_index)
    particle_index = max(noun_span) + 1
    if (
        particle_index >= predicate_index
        or tokens[particle_index][1] != "JX"
        or tokens[particle_index][0] not in SUBJECT_TOPIC_PARTICLES
    ):
        return False
    if _has_explicit_direct_object(tokens, particle_index + 1, predicate_index):
        return False
    if ENABLE_ROLE_NEUTRAL_AUXILIARY_PARTICLES:
        # default_object는 현재 Q 또는 역할을 보존한 이전 중심어에서 온다.
        # 동사 사전은 보지 않되, 현재 Q에서 확인한 술어 범위는 지킨다.
        question_role_matches = (
            default_object_predicates is None
            or base_verb in default_object_predicates
        )
        known_object_predicate = question_role_matches
    else:
        if _is_adjective_predicate(tokens, predicate_index):
            return False
        if base_verb in BARE_SUBJECT_VERBS:
            return False
        question_predicate_matches = (
            default_object_predicates is not None
            and base_verb in default_object_predicates
        )
        known_object_predicate = (
            question_predicate_matches
            and _verb_accepts_inferred_direct_object(base_verb)
        )
    # `구멍 난 잎은 ... 건강한 잎만 ...`처럼 같은 중심 명사가
    # 은/는과 만으로 대조되면 두 명사구는 같은 역할을 갖는 병렬
    # 대상으로 본다. 동사 사전에 없는 술어도 이 구조 근거로만 보완한다.
    restrictive_parallel = any(
        _is_noun_token(tokens[index][1])
        and _noun_name_at(tokens, index) == noun_name
        and index + 1 < len(tokens)
        and tokens[index + 1] == ("만", "JX")
        for index in range(len(tokens))
    )
    return known_object_predicate or restrictive_parallel


def _verb_accepts_inferred_direct_object(base_verb):
    """활용 여부와 무관하게 동사의 의미역 프레임에서 목적어 역할을 확인한다."""
    return "direct_object" in VERB_ARGUMENT_FRAMES.get(base_verb, ())


def _shareable_clause_connector_after(tokens, predicate_index):
    """다음 술어와 역할 공유 또는 차단을 결정할 절 경계를 반환한다."""
    connector_index = predicate_index + 1
    # `떨어뜨렸지만`처럼 과거·높임 선어말어미가 술어와 연결어미 사이에
    # 놓여도 실제 절 연결 표지까지 이동한다. 기존 다른 과거형 연결의
    # 공유 범위를 넓히지 않도록 이 건너뛰기는 대조 `-지만`에만 적용한다.
    while (
        connector_index < len(tokens)
        and tokens[connector_index][1].startswith("EP")
    ):
        connector_index += 1
    if connector_index >= len(tokens):
        return None
    connector, connector_pos = tokens[connector_index]
    if (
        connector_index > predicate_index + 1
        and connector not in CONTRASTIVE_OBJECT_CONNECTIVES
    ):
        return None
    if connector_pos.startswith("EC"):
        # `-고 나서`는 KKMA에서 고/EC + 나서/EC로 나뉜다. 일반적인
        # 병렬 `-고`보다 구체적인 직후 행동 표지를 먼저 확인한다.
        if (
            connector == "고"
            and connector_index + 1 < len(tokens)
            and tokens[connector_index + 1][0] == "나서"
            and tokens[connector_index + 1][1].startswith("EC")
        ):
            return "고 나서"
        if connector in SHAREABLE_CLAUSE_CONNECTIVES:
            return connector
        # 시간절은 주어 공유를 차단하기 위해 경계 유형을 보존한다.
        # 직접 표시된 목적어는 뒤 절에 새 목적어가 없을 때만 별도로 잇는다.
        for temporal_connector in TEMPORAL_SEQUENCE_CONNECTIVES:
            if connector.endswith(" " + temporal_connector):
                return temporal_connector
    return None


def _connector_allows_subject_inheritance(connector, subject_name):
    """절 유형과 앞 주어의 성격을 함께 보고 생략 주어 승계를 허용한다."""
    if connector in TEMPORAL_SEQUENCE_CONNECTIVES:
        return False
    if connector in REASON_CONNECTIVE_ENDINGS | IMMEDIATE_ACTION_CONNECTIVES:
        # `비가 와서 우산을 썼다`, `종이 울리자마자 나갔다`에서 비와
        # 종이 뒤 행동의 행위자로 뒤집히지 않게 한다.
        return _is_likely_person(subject_name)
    return connector in GENERAL_SHAREABLE_CLAUSE_CONNECTIVES


def _connector_allows_object_inheritance(connector):
    """명시됐던 앞 목적어를 허용된 연결 유형의 다음 절에만 잇는다."""
    return connector in OBJECT_SHAREABLE_CLAUSE_CONNECTIVES


def _is_temporal_clause_connector(word, pos):
    """후처리에서 접은 `-기 전`/`-은 뒤` 계열 시간절 경계인지 확인한다."""
    if not pos.startswith("EC"):
        return False
    return any(
        word == connector or word.endswith(" " + connector)
        for connector in TEMPORAL_SEQUENCE_CONNECTIVES
    )


def _subject_precedes_temporal_clause_boundary(tokens, subject_index):
    """주어가 뒤 본절이 아닌 앞 시간절 안에 있는지 보수적으로 판정한다."""
    for word, pos in tokens[subject_index + 1:]:
        if pos == "SF":
            break
        if _is_temporal_clause_connector(word, pos):
            return True
    return False


def _question_non_main_clause_indices(tokens):
    """Q의 관형절·시간절·조건절·이유절 내부 토큰을 반환한다.

    이 구간의 조사 역할은 질문 문장 안에서는 유효하지만 A의 생략
    주어·목적어를 채우는 후보로는 전달하지 않는다.
    """
    excluded = set()
    for span in _adnominal_clause_spans(tokens):
        excluded.update(range(span["start"], span["end"] + 1))

    clause_start = 0
    for index, (word, pos) in enumerate(tokens):
        if pos == "SF":
            clause_start = index + 1
            continue
        if not pos.startswith("EC"):
            continue
        is_conditional = word == "면" or word.endswith("으면")
        is_reason = word in REASON_CONNECTIVE_ENDINGS
        if not (
            _is_temporal_clause_connector(word, pos)
            or is_conditional
            or is_reason
        ):
            continue
        has_later_predicate = any(
            later_pos in {"VV", "VXV", "VA", "XSV", "XSA", "VCP"}
            for _, later_pos in tokens[index + 1:]
        )
        if has_later_predicate:
            excluded.update(range(clause_start, index + 1))
            clause_start = index + 1
    return excluded


def _question_confirmed_main_object_indices(
    tokens,
    default_object,
    question_required_roles=None,
):
    """Q와 A의 격표지가 함께 확인한 병렬 본절 목적어 범위를 반환한다.

    ``삽과 갈퀴는 어디에 두었나`` → ``삽과 갈퀴를 낡은 창고에
    정리했다``에서 ``갈퀴를`` 앞의 병렬군은 뒤 ``낡은``의 관형절
    논항이 아니라 본절 ``정리하다``의 목적어다. 반대로 ``철수와
    영희가 만든 음식``처럼 오른쪽 명사가 주격인 관형절 내부 병렬군과,
    Q가 주어 답을 요구하는 경우는 건드리지 않는다.

    형태소 분석기가 와/과를 실제 JC로 확인하고, 현재 Q의 중심 목적어와
    A의 오른쪽 JKO 명사가 일치할 때만 보호해 일반 문장으로의 확장을
    제한한다.
    """
    if (
        default_object is None
        or "subject" in set(question_required_roles or [])
    ):
        return set()

    target = re.sub(r"\s+", "", default_object.noun)
    if not target:
        return set()

    protected = set()
    for noun_index, (_, noun_pos) in enumerate(tokens):
        if not _is_noun_token(noun_pos):
            continue
        noun_end = _noun_phrase_right_edge(tokens, noun_index)
        particle_index = noun_end + 1
        if (
            particle_index >= len(tokens)
            or tokens[particle_index][1] != "JKO"
        ):
            continue

        noun_name = _noun_name_at(tokens, noun_end)
        compact_name = re.sub(r"\s+", "", noun_name)
        if not (
            compact_name == target
            or (
                min(len(compact_name), len(target)) >= 2
                and (compact_name in target or target in compact_name)
            )
        ):
            continue

        clause_start = 0
        for boundary_index in range(noun_index - 1, -1, -1):
            _, boundary_pos = tokens[boundary_index]
            if boundary_pos == "SF" or boundary_pos.startswith("EC"):
                clause_start = boundary_index + 1
                break

        # 대상 명사에 붙은 관형표현까지 하나의 완성된 명사구로 본다.
        # 그 명사구의 바로 왼쪽 연결조사만 본절 병렬군으로 인정해야
        # `사과와 배를 깎은 접시` 안의 와/과를 잘못 끌어오지 않는다.
        right_phrase_start = _modified_noun_phrase_left_edge(
            tokens,
            noun_end,
            clause_start,
        )
        connector_index = right_phrase_start - 1
        if (
            connector_index < clause_start
            or tokens[connector_index][1] != "JC"
            or tokens[connector_index][0] not in {"와", "과", "랑", "이랑"}
        ):
            continue

        left_noun_index = connector_index - 1
        if (
            left_noun_index < clause_start
            or not _is_noun_token(tokens[left_noun_index][1])
        ):
            continue
        group_start = _modified_noun_phrase_left_edge(
            tokens,
            left_noun_index,
            clause_start,
        )
        # A와 B와 C처럼 연속된 완성 명사구도 같은 역할 범위에 포함한다.
        while group_start - 2 >= clause_start:
            previous_connector = group_start - 1
            previous_noun = previous_connector - 1
            if (
                tokens[previous_connector][1] != "JC"
                or tokens[previous_connector][0]
                not in {"와", "과", "랑", "이랑"}
                or not _is_noun_token(tokens[previous_noun][1])
            ):
                break
            group_start = _modified_noun_phrase_left_edge(
                tokens,
                previous_noun,
                clause_start,
            )
        protected.update(range(group_start, particle_index + 1))

    return protected


def _subject_is_inside_copular_topic_description(tokens, subject_index):
    """`손이 많이 간 수리는 무엇인가`형 앞쪽 주어를 걸러낸다."""
    if not any(pos == "VCP" for _, pos in tokens[subject_index + 1:]):
        return False
    for index in range(subject_index + 2, len(tokens) - 1):
        if not _is_noun_token(tokens[index][1]):
            continue
        particle, particle_pos = tokens[index + 1]
        noun_name = _noun_name_at(tokens, index)
        if (
            particle_pos == "JX"
            and particle in SUBJECT_TOPIC_PARTICLES
            and noun_name not in TEMPORAL_NOUNS
            and noun_name not in QUESTION_GENERIC_NOUNS
        ):
            return True
    return False


def _shared_direct_object_is_compatible(base_verb, object_name):
    """앞 절 목적어가 뒤 동사의 필수 의미 유형과 맞는지 확인한다."""
    if base_verb == "만나다":
        return _is_likely_person(object_name)
    return True


def _beneficiary_core_verb(base_verb):
    """'알려주고 싶다'에서 희망 연산자를 제거해 '알려주다'를 복원한다."""
    desire_suffix = "고 싶다"
    if base_verb.endswith(desire_suffix):
        stem = base_verb[:-len(desire_suffix)].strip()
        return stem if stem.endswith("다") else stem + "다"
    return base_verb


def _is_beneficiary_verb(base_verb):
    """'-아/어 주다' 구성 또는 별도 수혜 의미 예외인지 판정한다."""
    core_verb = _beneficiary_core_verb(base_verb)
    return (
        core_verb.endswith("주다")
        or core_verb in BENEFICIARY_EXCEPTION_VERBS
    )


def _find_explicit_subject(tokens, start, end, excluded_indices=None):
    """현재 술어 구간에서 마지막으로 명시된 주어를 찾는다."""
    excluded_indices = set(excluded_indices or [])
    candidate = None
    candidate_index = None

    for index in range(start, end):
        word, pos = tokens[index]
        if (
            index in excluded_indices
            or not _is_noun_token(pos)
            or index + 1 >= end + 1
        ):
            continue

        next_word, next_pos = tokens[index + 1]
        # KKMA가 '사람들이', '관객들이'의 주격 조사 '이'를
        # 간혹 JX로 태깅하므로 답변에 명시된 주어로 함께 인정한다.
        is_subject = (
            next_pos == 'JKS'
            or (next_pos == 'JX' and next_word in {'이', '가'})
        )
        is_topic_subject = (
            next_pos == 'JX'
            and next_word in SUBJECT_TOPIC_PARTICLES
            and word not in TEMPORAL_NOUNS
        )
        is_additive_subject = (
            next_pos == 'JX'
            and next_word in ADDITIVE_PARTICLES
            and _infer_additive_role(tokens, index) == "subject"
        )
        # '선생님은 나를 목소리가 따뜻한 사람이라고 불렀다'의
        # '목소리가'는 본절이 아니라 관형형 형용사 절의 주어다.
        embedded_adnominal_subject = (
            candidate is not None
            and any(
                tokens[later_index][1] in {'VA', 'XSA'}
                and _verb_has_following_etd(tokens, later_index)
                for later_index in range(index + 2, end)
            )
        )
        if embedded_adnominal_subject:
            continue
        if is_subject or is_topic_subject or is_additive_subject:
            candidate = _noun_name_at(tokens, index)
            candidate_index = index

            # '철수라는 친구가'는 일반명사 '친구'보다 이름 '철수'를
            # 실제 관계 주어로 사용한다.
            if (
                is_subject
                and word in {"친구", "사람", "아이", "학생"}
                and index >= 3
                and tokens[index - 1][1] == 'ETD'
                and tokens[index - 2][1] == 'VCP'
                and _is_noun_token(tokens[index - 3][1])
            ):
                candidate = _noun_name_at(tokens, index - 3)
                candidate_index = index - 3
                INFERRED_PERSON_NAMES.add(candidate)

    return candidate, candidate_index


def _explicit_case_role_indices(tokens, start, end, excluded_indices=None):
    """현재 절에서 명시적인 이/가와 을/를 명사 위치를 분리한다."""
    excluded_indices = set(excluded_indices or [])
    roles = {"subject": [], "direct_object": []}
    seen_spans = set()

    for index in range(start, end):
        if not _is_noun_token(tokens[index][1]):
            continue
        noun_span = _noun_span_indices(tokens, index)
        if noun_span & excluded_indices:
            continue
        right_edge = max(noun_span)
        span_key = (min(noun_span), right_edge)
        if span_key in seen_spans or right_edge + 1 >= end + 1:
            continue
        seen_spans.add(span_key)
        particle_word, particle_pos = tokens[right_edge + 1]
        if particle_pos == "JKS" or (
            particle_pos == "JX" and particle_word in {"이", "가"}
        ):
            roles["subject"].append((right_edge, _noun_name_at(tokens, right_edge)))
        elif particle_pos == "JKO":
            roles["direct_object"].append(
                (right_edge, _noun_name_at(tokens, right_edge))
            )
    return roles


def _auxiliary_particle_role(
    tokens,
    noun_index,
    clause_start,
    predicate_index,
    excluded_indices=None,
    default_subject=None,
    focus_entity=None,
    default_object=None,
    default_object_predicates=None,
    base_verb=None,
    question_required_roles=None,
    relation_history=None,
):
    """은/는/도의 역할을 조사 밖의 명시적 근거로만 판정한다.

    이/가·을/를은 반대 역할을 배제하는 강한 근거로 사용한다. 그다음
    시간·위치, 현재 Q가 요구한 역할, 역할이 확인된 Q/중심어를 사용한다.
    근거가 없으면 기존 은/는 동작만 유지하고, 도는 역할을 확정하지 않는다.
    """
    if not (0 <= noun_index < predicate_index):
        return None
    noun_span = _noun_span_indices(tokens, noun_index)
    particle_index = max(noun_span) + 1
    if particle_index >= len(tokens):
        return None
    particle_word, particle_pos = tokens[particle_index]
    if not (
        particle_pos == "JX"
        and particle_word in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
    ):
        return None

    noun_name = _noun_name_at(tokens, max(noun_span))
    candidate_is_person = (
        _is_likely_person(noun_name)
        or any(tokens[index][1] == "NNP" for index in noun_span)
    )
    if noun_name in TEMPORAL_NOUNS or noun_name in positions:
        return "context"

    explicit = _explicit_case_role_indices(
        tokens,
        clause_start,
        predicate_index,
        excluded_indices=excluded_indices,
    )
    has_explicit_subject = bool(explicit["subject"])
    has_explicit_object = bool(explicit["direct_object"])

    # 현재 질문에서 직접목적어로 확인된 *같은 명사*의 역할이 가장
    # 강하다. `철수를 누가 도왔나 / 철수는 영희가 도왔다`에서는
    # 누가라는 빈 주어 요구보다 철수=목적어라는 명시 역할을 지킨다.
    if (
        default_object is not None
        and noun_name == default_object.noun
        and not has_explicit_object
        and (
            default_object_predicates is None
            or base_verb in default_object_predicates
        )
    ):
        return "direct_object"

    # 같은 명사의 명시 역할이 없으면 Q가 요구한 아직 비어 있는 성분을
    # 은/는 명사에 먼저 부여한다. 중심어 기억이나 기본 화자보다 현재
    # 질문의 조사(누가/무엇을)가 우선한다.
    required_roles = set(question_required_roles or [])
    if required_roles == {"subject"} and not has_explicit_subject:
        return "subject"
    if required_roles == {"direct_object"} and not has_explicit_object:
        return "direct_object"

    # Q에 빈 역할 요구가 없을 때 현재 질문에서 주어로 확인된 중심어
    # 또는 명시된 화자 주제를 사용한다.
    subject_names = {
        entity.noun
        for entity in (focus_entity, default_subject)
        if entity is not None
    }
    if noun_name in subject_names and not has_explicit_subject:
        return "subject"

    # 도는 앞에서 명시적으로 확인된 같은 격을 잇는 경우만 유지한다.
    if particle_word in ADDITIVE_PARTICLES:
        inherited_role = _infer_additive_role(tokens, noun_index)
        if inherited_role == "context":
            return "context"
        if inherited_role == "subject" and not has_explicit_subject:
            return "subject"
        if inherited_role == "direct_object" and not has_explicit_object:
            return "direct_object"

        # 현재 Q에 같은 술어의 목적어가 명시되어 있으면, 같은 유형의
        # 새 `도` 명사는 그 목적어에 추가된 대상으로 볼 수 있다.
        # `사과를 먹었나 / 배도 먹었다`는 허용하되, `철수가 사과를
        # 먹었나 / 영희도 먹었다`의 영희를 사과 자리에 넣지는 않는다.
        question_object_matches = (
            default_object is not None
            and not has_explicit_object
            and (
                default_object_predicates is None
                or base_verb in default_object_predicates
            )
            and (
                candidate_is_person
                == _is_likely_person(default_object.noun)
            )
        )
        if question_object_matches:
            return "direct_object"

        # Q에 주격으로 잡힌 인물과 같은 유형의 새 인물은 주어를
        # 추가한 것으로 본다. 비인물 focus는 주제/대상일 수도 있어
        # 이 규칙으로 주어화하지 않는다.
        if (
            focus_entity is not None
            and candidate_is_person
            and not has_explicit_subject
        ):
            return "subject"

        historical_role = _recent_same_predicate_additive_role(
            relation_history,
            base_verb,
            noun_name,
            candidate_is_person=candidate_is_person,
        )
        if historical_role == "subject" and not has_explicit_subject:
            return "subject"
        if historical_role == "direct_object" and not has_explicit_object:
            return "direct_object"

    # 을/를이 따로 있으면 보조사 명사는 목적어가 아니다. 이/가도 따로
    # 없다면 남은 주어 자리를 채울 수 있다. 반대 방향은 목적어라고
    # 확정하지 않고 Q/중심어 근거가 있을 때만 위에서 처리한다.
    if has_explicit_object and not has_explicit_subject:
        return "subject"
    # 위 근거로 다른 역할이 확인되지 않은 은/는은 기존 결과를 보존한다.
    # 도는 같은 역할을 확인하지 못했으므로 여기서 주어로 고정하지 않는다.
    if (
        particle_word in SUBJECT_TOPIC_PARTICLES
        and not has_explicit_subject
    ):
        return "subject"
    return None


def _collect_auxiliary_particle_roles(
    tokens,
    start,
    end,
    excluded_indices=None,
    **role_context,
):
    """현재 절의 보조사 명사를 역할별 후보로 모은다."""
    excluded_indices = set(excluded_indices or [])
    roles = {"subject": [], "direct_object": [], "context": []}
    seen_spans = set()
    for index in range(start, end):
        if not _is_noun_token(tokens[index][1]):
            continue
        noun_span = _noun_span_indices(tokens, index)
        if noun_span & excluded_indices:
            continue
        right_edge = max(noun_span)
        span_key = (min(noun_span), right_edge)
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        role = _auxiliary_particle_role(
            tokens,
            right_edge,
            start,
            end,
            excluded_indices=excluded_indices,
            **role_context,
        )
        if role in roles:
            roles[role].append(
                (right_edge, _noun_name_at(tokens, right_edge), noun_span)
            )
    return roles


def _normalize_relation_verb(tokens, index, explicit_subject_name=None, compound_verb=None):
    """KKMA가 활용형/복합동사를 잘못 자른 경우를 관계용 기본형으로 보정한다."""
    if compound_verb is not None:
        return compound_verb

    word, _ = tokens[index]
    if word == "스":
        return "서다"
    if word == "듣" and explicit_subject_name in {"마음", "정"}:
        return "들다"
    if (
        word == "피"
        and index >= 2
        and tokens[index - 2][0] == "넓"
        and tokens[index - 1][0] == "게"
    ):
        return "펴다"
    if word == "길르":
        return "기르다"
    if (
        tokens[index][1] == "XSV"
        and word == "하"
        and index > 0
        and _is_noun_token(tokens[index - 1][1])
    ):
        # 연속 명사는 역할명에서는 하나의 명사구로 묶되, XSV 바로 앞
        # 명사만 파생동사의 어간이다. 예: "시장 구경하다" -> 구경하다.
        return tokens[index - 1][0] + "하다"
    if word == "있었":
        return "있다"
    if word == "없었":
        return "없다"
    if word == "갈" and index + 1 < len(tokens) and tokens[index + 1][1] == 'ETD':
        return "가다"
    if word == "날" and explicit_subject_name in {"생각", "기억"}:
        return "나다"
    if word in {"나", "날"} and index > 0 and tokens[index - 1][0] == "고장":
        return "고장나다"
    if word == "파" and _verb_has_following_etd(tokens, index):
        return "팔다"
    if (
        word == "사"
        and _verb_has_following_etd(tokens, index)
        and index > 0
        and _is_noun_token(tokens[index - 1][1])
        and _is_likely_person(_noun_name_at(tokens, index - 1))
    ):
        return "살다"

    # 어근/부사와 용언이 분리된 합성 술어를 복원한다.
    if (
        word == "해지"
        and index > 0
        and tokens[index - 1][1] in {'XR', 'NNG'}
    ):
        return tokens[index - 1][0] + "해지다"
    if word == "하" and index > 0 and tokens[index - 1][1] == 'XR':
        return tokens[index - 1][0] + "하다"
    if (
        word == "하"
        and index >= 2
        and tokens[index - 1][1] == 'MAG'
        and tokens[index - 2][1] == 'MAG'
        and tokens[index - 1][0] == tokens[index - 2][0]
    ):
        return tokens[index - 1][0] * 2 + "하다"

    # "생각 + 나다", "기억 + 나다"는 하나의 심리 동사로 취급한다.
    if word in {"나", "날"} and index > 0:
        prev_word, prev_pos = tokens[index - 1]
        if (
            _is_noun_token(prev_pos)
            and prev_word in {"생각", "기억"}
            and not (
                index < len(tokens)
                and tokens[index - 1][0] == explicit_subject_name
            )
        ):
            return prev_word + "나다"

    return get_base_form(word, 'VV')


def _verb_has_following_etd(tokens, verb_index):
    """선어말어미가 끼어 있어도 해당 동사의 관형사형 어미를 찾는다."""
    for index in range(verb_index + 1, len(tokens)):
        _, pos = tokens[index]
        if pos == 'ETD':
            return True
        if (
            pos == 'SF'
            or pos in {'VV', 'VXV', 'VX', 'VA', 'XSA'}
            or _is_noun_token(pos)
        ):
            return False
    return False


def _find_relative_head(tokens, verb_index):
    """관형절 뒤의 중심 명사와 조사 위치를 반환한다."""
    etd_index = None
    for index in range(verb_index + 1, len(tokens)):
        _, pos = tokens[index]
        if pos == 'ETD':
            etd_index = index
            break
        if (
            pos == 'SF'
            or pos in {'VV', 'VXV', 'XSV', 'VA', 'XSA'}
            or _is_noun_token(pos)
        ):
            return None, None

    if etd_index is None:
        return None, None

    fallback_head = None
    for index in range(etd_index + 1, len(tokens)):
        _, pos = tokens[index]
        if _is_noun_token(pos):
            next_pos = tokens[index + 1][1] if index + 1 < len(tokens) else None
            # '친절한 아주머니', '고운 한복'처럼 관형절 사이에 다른
            # 수식 표현이 끼면 조사가 붙은 뒤쪽 명사를 실제 중심어로 삼는다.
            if next_pos is not None and next_pos.startswith("J"):
                return _noun_name_at(tokens, index), index
            if next_pos not in {'XSA', 'XSV'}:
                fallback_head = (_noun_name_at(tokens, index), index)
        if pos == 'SF' or pos in {'VV', 'VXV'}:
            break
    return fallback_head or (None, None)


def _find_local_adnominal_adjective_subject(
    tokens,
    search_floor,
    adjective_index,
):
    """형용사 관형절에 직접 드러난 가장 가까운 주어·주제어를 찾는다.

    문장 앞쪽의 본절 주어까지 형용사절 주어로 끌어오지 않도록 형용사에서
    거꾸로 보았을 때 가장 가까운 조사 부착 논항만 확인한다. 그 논항이
    목적격·부사격이면 `작은 상자` 같은 단순 수식으로 보고 절 범위를
    만들지 않는다.
    """
    for index in range(adjective_index - 1, search_floor - 1, -1):
        if not _is_noun_token(tokens[index][1]):
            continue

        particle_index = index + 1
        if particle_index >= adjective_index:
            continue
        particle, particle_pos = tokens[particle_index]
        if not particle_pos.startswith("J"):
            continue

        if particle_pos == "JKS" or (
            particle_pos == "JX"
            and particle in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
        ):
            noun_name = _noun_name_at(tokens, index)
            # `지금도 고마운 사람`의 `지금도`는 시간 부사어이지
            # 형용사절의 명시 주어가 아니다.
            if noun_name in TEMPORAL_NOUNS and particle_pos == "JX":
                continue
            return noun_name, index

        # 형용사보다 가까운 목적어·부사어가 있으면 앞의 본절 주어는
        # 이 형용사에 명시된 주어로 보지 않는다.
        return None, None

    return None, None


def _adnominal_successor_search_floor(
    tokens,
    search_floor,
    predicate_index,
    previous_predicate_index=None,
):
    """앞 관형형의 중심 명사를 뒤 관형절 범위에서 기본 제외한다.

    ``[빈] 밥그릇에 [마른] 사료``에서 ``밥그릇에``는 이미 첫 관형형의
    피수식 명사구다. ETD 직후만 다음 탐색 하한으로 사용하면 이 명사구가
    ``마른``의 문맥으로 다시 소비된다. 앞 중심어에 이/가·을/를이 붙어
    뒤 관형 술어의 핵심 논항임이 명시된 경우에만 재사용한다.
    """
    if (
        not ENABLE_ADNOMINAL_HEAD_SCOPE_GUARD
        or previous_predicate_index is None
    ):
        return search_floor

    _, previous_head_index = _find_relative_head(
        tokens,
        previous_predicate_index,
    )
    if (
        previous_head_index is None
        or previous_head_index < search_floor
        or previous_head_index >= predicate_index
    ):
        return search_floor

    previous_head_end = _noun_phrase_right_edge(tokens, previous_head_index)
    next_index = previous_head_end + 1
    if next_index < predicate_index and tokens[next_index][1] in {"JKS", "JKO"}:
        return search_floor

    # 에+는처럼 조사가 연달아 붙는 경우도 한 명사구의 꼬리로 함께
    # 넘긴다. 부사·새 명사 등은 다음 관형형의 독립 범위에 남긴다.
    while next_index < predicate_index and tokens[next_index][1].startswith("J"):
        next_index += 1
    return max(search_floor, next_index)


def _adnominal_clause_boundary_floor(tokens, start, predicate_index):
    """시간·연결절 뒤에서 관형절의 새 시작 위치를 반환한다.

    앞 절의 주어가 뒤 관형 술어의 주어로 흡수되지 않게 가장 가까운 강한
    연결 경계까지만 이동한다. `-고 있다`처럼 오른쪽 술어가 보조용언이면
    하나의 복합 술어이므로 경계로 사용하지 않는다.
    """
    if not ENABLE_ADNOMINAL_CONNECTOR_BOUNDARY:
        return start

    boundary_floor = start
    predicate_positions = {"VV", "VXV", "XSV", "VA", "XSA"}

    for previous_index in range(start, predicate_index):
        if tokens[previous_index][1] not in predicate_positions:
            continue
        connector = _shareable_clause_connector_after(tokens, previous_index)
        if connector not in ADNOMINAL_CLAUSE_BOUNDARY_CONNECTIVES:
            continue

        connector_index = previous_index + 1
        while (
            connector_index < predicate_index
            and tokens[connector_index][1].startswith("EP")
        ):
            connector_index += 1
        if connector_index >= predicate_index:
            continue

        connector_end = connector_index
        if connector == "고 나서":
            connector_end += 1
        if connector_end >= predicate_index:
            continue

        next_predicate_index = connector_end + 1
        if (
            next_predicate_index == predicate_index
            and tokens[predicate_index][1] in {"VXV", "VXA", "VX"}
        ):
            continue

        boundary_floor = max(boundary_floor, connector_end + 1)

    return boundary_floor


def _adnominal_clause_spans(
    tokens,
    reserved_main_subject_indices=None,
    reserved_main_role_indices=None,
):
    """ETD를 끝으로 하는 관형절의 최소 토큰 범위를 계산한다.

    관계 생성과 동일한 `_find_relative_clause_start()`를 사용해 절 시작점을
    정한다. 관계 생성용 범위와 연결조사·역할 분리용 범위가 서로 달라져
    본절 목적어를 삭제하는 일을 막는다.
    """
    reserved_main_subject_indices = set(reserved_main_subject_indices or [])
    reserved_main_role_indices = set(reserved_main_role_indices or [])
    spans = []
    previous_adnominal_end = -1
    previous_adnominal_predicate = None
    predicate_positions = {"VV", "VXV", "XSV", "VA", "XSA"}

    for ending_index, (_, ending_pos) in enumerate(tokens):
        if ending_pos != "ETD":
            continue

        search_floor = previous_adnominal_end + 1
        predicate_index = None
        for index in range(ending_index - 1, search_floor - 1, -1):
            if tokens[index][1] in predicate_positions:
                predicate_index = index
                break
        if predicate_index is None:
            previous_adnominal_end = ending_index
            continue

        search_floor = _adnominal_successor_search_floor(
            tokens,
            search_floor,
            predicate_index,
            previous_adnominal_predicate,
        )
        # `작업대가 흔들린 뒤 [뜨거운 병]`, `연기가 나서 [빨간
        # 스위치]`처럼 절 경계 바로 뒤에 형용사 관형형이 오면, 앞 절의
        # 이/가 주어를 그 형용사의 지역 주어로 다시 끌어오지 않는다.
        # 동사 관형형은 _find_relative_clause_start() 안에서 같은 경계를
        # 적용하지만 형용사 관형형은 그 경로를 타지 않으므로, 두 형식을
        # 동일한 범위 규칙으로 맞춘다.
        search_floor = _adnominal_clause_boundary_floor(
            tokens,
            search_floor,
            predicate_index,
        )
        protected_before_predicate = [
            index
            for index in reserved_main_role_indices
            if search_floor <= index < predicate_index
        ]
        if protected_before_predicate:
            # Q와 JKO가 본절 목적어로 함께 확정한 병렬 명사군은 뒤쪽
            # 관형 술어의 내부 범위로 다시 가져가지 않는다.
            search_floor = max(
                search_floor,
                max(protected_before_predicate) + 1,
            )
        previous_adnominal_predicate = predicate_index

        predicate_pos = tokens[predicate_index][1]
        predicate_is_adjective = _is_adjective_predicate(
            tokens,
            predicate_index,
        )
        if predicate_is_adjective:
            explicit_subject_name, explicit_subject_index = (
                _find_local_adnominal_adjective_subject(
                    tokens,
                    search_floor,
                    predicate_index,
                )
            )
            # `작은 상자`처럼 주어가 직접 나타나지 않은 형용사 관형형은
            # 명사 속성 연결만 담당하고 관형절 소속 범위는 만들지 않는다.
            if explicit_subject_name is None:
                previous_adnominal_end = ending_index
                continue
            # `농부가 넓은 마당에서 ...`처럼 문두 주어 뒤에 단순
            # 형용사 수식이 붙은 경우는 문법 형태만으로 `[농부가 넓은]`과
            # 구별할 수 없다. 뒤에 다시 관형 동사가 이어지는 중첩 구조이거나
            # 앞에 별도 본절 주어가 이미 있는 경우만 완료 뒤 마스킹한다.
            preceding_subject_name, preceding_subject_index = _find_explicit_subject(
                tokens,
                search_floor,
                explicit_subject_index,
            )
            preceding_subject_in_same_clause = (
                preceding_subject_name is not None
                and preceding_subject_index is not None
                and not any(
                    tokens[between_index][1]
                    in {"VV", "VXV", "XSV", "VA", "XSA"}
                    for between_index in range(
                        preceding_subject_index + 1,
                        explicit_subject_index,
                    )
                )
            )
            later_adnominal_verb = any(
                tokens[later_index][1] in {"VV", "VXV", "XSV"}
                and _verb_has_following_etd(tokens, later_index)
                for later_index in range(ending_index + 1, len(tokens))
                if not any(
                    boundary_pos == "SF"
                    for _, boundary_pos in tokens[ending_index + 1:later_index]
                )
            )
            mask_after_completion = (
                preceding_subject_in_same_clause
                or later_adnominal_verb
            )
        else:
            explicit_subject_name, explicit_subject_index = _find_explicit_subject(
                tokens,
                search_floor,
                predicate_index,
            )
            mask_after_completion = True
        base_verb = _normalize_relation_verb(
            tokens,
            predicate_index,
            explicit_subject_name=explicit_subject_name,
        )
        _, relative_head_index = _find_relative_head(
            tokens,
            predicate_index,
        )
        if predicate_is_adjective:
            clause_start = explicit_subject_index
            if explicit_subject_index in reserved_main_subject_indices:
                # Q가 요구한 본절 주어를 단순 관형 수식 범위에서 보호한다.
                clause_start = predicate_index
        else:
            clause_start = _find_relative_clause_start(
                tokens,
                search_floor,
                predicate_index,
                base_verb,
                relative_head_index=relative_head_index,
                reserved_main_subject_indices=reserved_main_subject_indices,
                reserved_main_role_indices=reserved_main_role_indices,
            )
        spans.append(
            {
                "start": clause_start,
                "predicate": predicate_index,
                "end": ending_index,
                "type": "relative",
                "mask_after_completion": mask_after_completion,
            }
        )
        previous_adnominal_end = ending_index

    return spans


def _completed_adnominal_indices(adnominal_spans, predicate_index):
    """현재 술어보다 앞에서 끝난 관형절의 내부 토큰을 반환한다.

    `[다리가 굽은] [탁자를 고치는] 일`에서 첫 관형절의 지역 주어
    `다리`가 뒤의 `고치다` 주어로 다시 선택되지 않게 한다. 관형절의
    중심 명사는 ETD 뒤에 있으므로 이 범위에 포함되지 않아 뒤 절의
    주어·목적어 후보로 계속 사용할 수 있다.
    """
    return {
        index
        for span in adnominal_spans
        if (
            span["end"] < predicate_index
            and span.get("mask_after_completion", True)
        )
        for index in range(span["start"], span["end"] + 1)
    }


def _belongs_to_later_adnominal_clause(
    tokens,
    candidate_start,
    candidate_end,
    current_head_index,
):
    """연결조사 오른쪽 명사가 뒤의 별도 관형절 논항인지 확인한다."""
    return any(
        span["predicate"] > current_head_index
        and span["start"] <= candidate_start
        and candidate_end <= span["end"]
        for span in _adnominal_clause_spans(tokens)
    )


def _belongs_to_other_adnominal_clause(
    tokens,
    noun_index,
    predicate_index,
    adnominal_spans=None,
):
    """현재 술어와 다른 관형절에 속한 명사인지 확인한다.

    본절 관계를 만들 때 다른 관형절의 목적어가 섞이거나, 한 관형절의
    목적어가 앞 관형절의 병렬 명사로 확장되는 것을 막는다. `먹으며
    나누는 일`처럼 연결어미로 이어진 동사는 하나의 관형절 범위 안에
    있으므로, 같은 관형절 범위에 든 명사는 유지한다.
    """
    spans = (
        adnominal_spans
        if adnominal_spans is not None
        else _adnominal_clause_spans(tokens)
    )
    return any(
        span["start"] <= noun_index <= span["end"]
        and not (span["start"] <= predicate_index <= span["end"])
        for span in spans
    )


def _relative_attachment_names(tokens, head_name, head_index):
    """관형절 부착 대상과 같은 문장 역할을 공유할 병렬 명사를 반환한다."""
    names = [head_name]
    role_names = [head_name]
    if head_index is None or head_index + 1 >= len(tokens):
        return names, role_names

    coordination_anchor = head_index
    coordination_shares_head_role = True
    if tokens[head_index + 1][1] == "JKG":
        coordination_shares_head_role = False
        for index in range(head_index + 2, len(tokens)):
            _, pos = tokens[index]
            if pos == "SF" or pos in {"VV", "VXV", "XSV"}:
                break
            if not _is_noun_token(pos):
                continue
            next_pos = (
                tokens[index + 1][1]
                if index + 1 < len(tokens)
                else None
            )
            if next_pos in {"XSA", "XSV"}:
                continue
            coordination_anchor = _noun_phrase_right_edge(tokens, index)
            carrier_name = _noun_name_at(tokens, coordination_anchor)
            if carrier_name not in names:
                names.append(carrier_name)
            break

    # `줄 연필과 공책`, `고장 난 대문과 의자`처럼 하나의 관형절 뒤에
    # 바로 이어지는 병렬 명사는 모두 같은 수식 범위로 본다. 연결조사
    # 오른쪽에서 새 술어·관형 표현이 먼저 시작되면 별도 범위이므로 멈춘다.
    while coordination_anchor + 2 < len(tokens):
        connector_word, connector_pos = tokens[coordination_anchor + 1]
        if (
            connector_word not in {"와", "과", "랑", "이랑"}
            or connector_pos not in {"JC", "JKM"}
        ):
            break

        candidate_start = coordination_anchor + 2
        if _is_noun_token(tokens[candidate_start][1]):
            candidate_end = _noun_phrase_right_edge(tokens, candidate_start)
        elif (
            _is_noun_phrase_modifier(tokens, candidate_start)
            and candidate_start + 1 < len(tokens)
            and _is_noun_token(tokens[candidate_start + 1][1])
        ):
            candidate_end = _noun_phrase_right_edge(tokens, candidate_start + 1)
        else:
            break

        particle_index = candidate_end + 1
        particle_pos = (
            tokens[particle_index][1]
            if particle_index < len(tokens)
            else None
        )
        if _belongs_to_later_adnominal_clause(
            tokens,
            candidate_start,
            candidate_end,
            coordination_anchor,
        ):
            # `만든 의자와 책을 읽는 아이`의 책처럼 뒤 관형절 안에서
            # 역할이 확인된 명사는 앞 관형절의 병렬 중심어로 확장하지 않는다.
            break

        candidate_name = _noun_name_at(tokens, candidate_end)
        if (
            particle_pos == "JKO"
            and _is_likely_person(head_name)
            != _is_likely_person(candidate_name)
        ):
            # `앉은 아주머니와 이야기를 나누다`의 와/과는 병렬 목적어가
            # 아니라 동반자를 표시한다. 서로 다른 인물성 범주면 넘기지 않는다.
            break
        if candidate_name not in names:
            names.append(candidate_name)
        if coordination_shares_head_role and candidate_name not in role_names:
            role_names.append(candidate_name)
        coordination_anchor = candidate_end
    return names, role_names


def _adnominal_object_complement_start(
    tokens,
    start,
    predicate_index,
    head_index,
):
    """목적격보어 관형형 앞의 본절 목적어 다음 위치를 반환한다.

    `철수를 동네에서 봉사하는 반장으로 뽑다`와 `철수를 손이 큰
    아이로 부르다`는 관형 술어의 종류만 다르고 경계 구조는 같다.
    중심 명사에 로/으로가 붙으면 관형 술어 앞의 첫 명시적 을/를은
    본절 목적어로 보존하고, 그 조사 다음부터 관형 수식 범위로 삼는다.
    """
    if head_index is None:
        return None

    head_end = _noun_phrase_right_edge(tokens, head_index)
    particle_index = head_end + 1
    if particle_index >= len(tokens):
        return None
    particle_word, particle_pos = tokens[particle_index]
    if (
        particle_word not in OBJECT_COMPLEMENT_PARTICLES
        or not particle_pos.startswith("J")
    ):
        return None

    index = start
    while index < predicate_index:
        if not _is_noun_token(tokens[index][1]):
            index += 1
            continue
        noun_end = _noun_phrase_right_edge(tokens, index)
        role_particle_index = noun_end + 1
        if role_particle_index >= predicate_index:
            break
        if tokens[role_particle_index][1] == "JKO":
            return role_particle_index + 1
        index = max(index + 1, noun_end + 1)
    return None


def _coordinated_noun_phrase_boundary_before_adnominal(
    tokens,
    start,
    predicate_index,
    relative_head_index,
):
    """`[명사구]와 [관형표현+명사구]`의 두 번째 범위를 제한한다.

    오른쪽 관형 술어 앞까지 명시적인 주격·목적격 논항이 없고, 관형
    중심 명사가 뒤에서 실제 조사를 받으면 연결조사 양쪽을 완성된
    명사구로 본다. `철수와 영희가 만든 음식`, `사과와 배를 깎은
    접시`처럼 연결조사 뒤 논항에 이미 격조사가 붙은 경우에는 관형절
    내부 연결이므로 경계로 사용하지 않는다.
    """
    if relative_head_index is None:
        return None

    head_end = _noun_phrase_right_edge(tokens, relative_head_index)
    particle_index = head_end + 1
    if (
        particle_index >= len(tokens)
        or not tokens[particle_index][1].startswith("J")
        or tokens[particle_index][1] == "JKG"
    ):
        return None

    connector_index = None
    for index in range(predicate_index - 1, start - 1, -1):
        word, pos = tokens[index]
        if pos == "JC" and word in {"와", "과", "랑", "이랑"}:
            connector_index = index
            break
        if (
            pos == "SF"
            or pos.startswith("EC")
            or pos.startswith("J")
            or _is_noun_token(pos)
            or pos in {"VV", "VXV", "VX", "VA", "XSV", "XSA", "VCP"}
        ):
            return None

    if (
        connector_index is None
        or connector_index <= start
        or not _is_noun_token(tokens[connector_index - 1][1])
    ):
        return None

    left_name = _noun_name_at(tokens, connector_index - 1)
    right_name = _noun_name_at(tokens, relative_head_index)
    if _is_likely_person(left_name) and not _is_likely_person(right_name):
        # `친구와 만든 음식`, `할머니와 구운 고구마`처럼 사람+사물
        # 조합은 완성 명사구 두 개보다 관형절 내부 동반 관계일 가능성이
        # 높다. 기존 인물성 판정을 재사용해 흔한 과분리를 막는다.
        return None

    # `친구와 함께 만든 음식`의 와/과는 두 명사구의 동등 연결이
    # 아니라 관형절 내부의 동반자를 나타내므로 범위를 끊지 않는다.
    if any(
        word in {"함께", "같이", "서로"}
        for word, _ in tokens[connector_index + 1:predicate_index]
    ):
        return None

    return connector_index + 1


def _find_relative_clause_start(
    tokens,
    start,
    verb_index,
    base_verb,
    relative_head_index=None,
    reserved_main_subject_indices=None,
    reserved_main_role_indices=None,
):
    """본절 선행 역할을 제외한 관형절 내부 시작 위치를 추정한다."""
    reserved_main_subject_indices = set(reserved_main_subject_indices or [])
    reserved_main_role_indices = set(reserved_main_role_indices or [])
    start = _adnominal_clause_boundary_floor(tokens, start, verb_index)

    # 형용사는 직접목적어를 논항으로 받지 않는다. `작은 부품들을
    # 투명한 상자에 모았다`에서 부품들을 `투명한 상자`의 내부
    # 목적어로 흡수하면, 뒤 본절의 목적어가 사라져 앞 시간절의
    # `실험`을 잘못 승계한다. 최종적으로 형용사로 판정된 관형
    # 술어에 한해서 가장 가까운 을/를 뒤를 범위 시작으로 삼고,
    # 목적격 명사구는 바깥 절의 역할 후보로 남긴다.
    if _is_jko_bounded_adjective_predicate(tokens, verb_index, start):
        for particle_index in range(verb_index - 1, start - 1, -1):
            _, particle_pos = tokens[particle_index]
            if particle_pos == "JKO":
                start = particle_index + 1
                break
            if particle_pos == "SF" or particle_pos.startswith("EC"):
                break

    noun_phrase_boundary = (
        _coordinated_noun_phrase_boundary_before_adnominal(
            tokens,
            start,
            verb_index,
            relative_head_index,
        )
    )
    if noun_phrase_boundary is not None:
        start = max(start, noun_phrase_boundary)
    protected_before_predicate = [
        index
        for index in reserved_main_role_indices
        if start <= index < verb_index
    ]
    if protected_before_predicate:
        start = max(start, max(protected_before_predicate) + 1)

    def after_reserved_subject(clause_start):
        """보호한 본절 주어와 바로 뒤 조사를 관형절 범위에서 제외한다."""
        reserved = [
            subject_index
            for subject_index in reserved_main_subject_indices
            if clause_start <= subject_index < verb_index
        ]
        if not reserved:
            return clause_start
        subject_index = max(reserved)
        particle_index = subject_index + 1
        if (
            particle_index < verb_index
            and tokens[particle_index][1].startswith("J")
        ):
            return particle_index + 1
        return subject_index + 1

    complement_start = _adnominal_object_complement_start(
        tokens,
        start,
        verb_index,
        relative_head_index,
    )
    if complement_start is not None:
        return after_reserved_subject(complement_start)

    _, subject_index = _find_explicit_subject(tokens, start, verb_index)
    if subject_index is None:
        return after_reserved_subject(start)

    relative_start = subject_index
    if (
        subject_index >= start + 2
        and tokens[subject_index - 1][1] in {"JC", "JKM"}
        and _is_noun_token(tokens[subject_index - 2][1])
    ):
        relative_start = subject_index - 2

    # '어머니가 나에게 반죽 빚는 법을 알려주다'에서 어머니/나에게는
    # 빚다의 논항이 아니라 뒤의 알려주다에 속한다. 관형절 동사가 수혜
    # 동사가 아닌데 명시 주어 뒤에 간접격과 무조사 명사가 이어지면
    # 간접격 다음 명사부터 관형절 내부로 본다.
    if not _is_beneficiary_verb(base_verb):
        for index in range(subject_index + 1, verb_index - 1):
            if not _is_noun_token(tokens[index][1]):
                continue
            particle_word, particle_pos = tokens[index + 1]
            if (
                particle_word in INDIRECT_PARTICLES
                and particle_pos.startswith("J")
                and any(
                    _is_noun_token(tokens[later][1])
                    for later in range(index + 2, verb_index)
                )
            ):
                return after_reserved_subject(index + 2)

    # `해가 바다 위로 떠오르는 모습`처럼 내용 명사를 꾸미고 중간에
    # 목적어가 없는 경우에는 첫 주격 명사가 관형절의 행위자다.
    # 단순히 `바다`부터 자르면 해가 사라지므로 지역 주어를 보존한다.
    relative_head_name = (
        _noun_name_at(tokens, relative_head_index)
        if relative_head_index is not None
        else None
    )
    if (
        relative_head_name in RELATIVE_CONTENT_NOUNS
        and not _has_explicit_direct_object(
            tokens,
            subject_index + 1,
            verb_index,
        )
    ):
        return after_reserved_subject(relative_start)

    # `[철수가 만든] 음식이 [손님을 만족시키는] 요리`처럼 앞 관형형의
    # 중심 명사가 이/가를 달고 곧바로 다음 관형절의 주어가 된 경우다.
    # 피수식 명사 재사용을 기본 차단하더라도 이 명시적 연결은 보존한다.
    subject_left = _noun_phrase_left_edge(tokens, subject_index)
    if (
        subject_index == start
        and subject_left > 0
        and tokens[subject_left - 1][1] == "ETD"
        and subject_index + 1 < verb_index
        and tokens[subject_index + 1][1] == "JKS"
    ):
        return after_reserved_subject(relative_start)

    # 본절 앞 주어를 관형절 안으로 자동 편입하지 않는다. 주어 뒤부터
    # 관형 동사 전까지 별도 명사구가 있으면 그 첫 명사구를 관형절의
    # 시작으로 삼는다.
    #
    #   한 어부가 [조개로 만든] 목걸이를 선물하다
    #   철수가 [영희가 만든] 음식을 먹다
    #
    # 중간 명사가 없는 `어머니가 만든 음식`은 기존처럼 어머니부터
    # 관형절로 유지한다. 선택된 범위 안에 주어가 없더라도 본절 주어는
    # 관형절 주어로 전달하지 않고 기존 미확정 주어 처리를 따른다.
    for index in range(subject_index + 1, verb_index):
        if not _is_noun_token(tokens[index][1]):
            continue
        # `거동이 불편한`, `상태가 안전한`처럼 명사 어근+XSV가 관형
        # 술어를 이룰 때 어근 자체를 별도 관형절 논항으로 오인하지 않는다.
        if (
            tokens[verb_index][1] == "XSV"
            and index == verb_index - 1
        ):
            continue
        noun_start = _noun_phrase_left_edge(tokens, index)
        if noun_start <= subject_index:
            continue
        return after_reserved_subject(noun_start)

    return after_reserved_subject(relative_start)


def _modified_noun_phrase_left_edge(tokens, noun_index, start=0):
    """명사에 바로 붙은 하나 이상의 관형표현을 포함한 왼쪽 경계다."""
    left_edge = _noun_phrase_left_edge(tokens, noun_index)
    predicate_positions = {"VV", "VXV", "VA", "XSV", "XSA"}

    while left_edge > start and tokens[left_edge - 1][1] == "ETD":
        ending_index = left_edge - 1
        predicate_index = None
        for index in range(ending_index - 1, start - 1, -1):
            _, pos = tokens[index]
            if pos in predicate_positions:
                predicate_index = index
                break
            if pos == "SF" or pos.startswith("EC") or _is_noun_token(pos):
                break
        if predicate_index is None:
            break

        local_floor = start
        for index in range(predicate_index - 1, start - 1, -1):
            _, pos = tokens[index]
            if pos == "ETD" or pos == "SF" or pos.startswith("EC"):
                local_floor = index + 1
                break

        base_verb = _normalize_relation_verb(
            tokens,
            predicate_index,
            explicit_subject_name=None,
        )
        modifier_start = _find_relative_clause_start(
            tokens,
            local_floor,
            predicate_index,
            base_verb,
            relative_head_index=noun_index,
        )
        if modifier_start >= left_edge:
            break
        left_edge = modifier_start

    return left_edge


def _find_light_verb_object(tokens, start, verb_index):
    """'구슬치기도 하고', '딱지도 하다' 같은 하다 구문의 목적어를 찾는다."""
    # KKMA: 구슬/NNG + 치/VV + 기/ETN + 를/JKO + 많이/MAG + 하/VV
    for index in range(start, verb_index - 2):
        if (
            _is_noun_token(tokens[index][1])
            and index + 3 < verb_index
            and tokens[index + 1][1] == 'VV'
            and tokens[index + 2] == ("기", "ETN")
            and tokens[index + 3][1] == 'JKO'
        ):
            action = _noun_name_at(tokens, index) + tokens[index + 1][0] + "기"
            return action, {index, index + 1, index + 2}

    # KKMA: 구슬/NNG + 하/XSV + 지/ECD + 기도/NNG + 하/VV
    if verb_index >= 4:
        first_word, first_pos = tokens[verb_index - 4]
        if (
            _is_noun_token(first_pos)
            and tokens[verb_index - 3][1] == 'XSV'
            and tokens[verb_index - 2][1].startswith('EC')
            and tokens[verb_index - 1][0] == "기도"
        ):
            return first_word + "치기", {verb_index - 4, verb_index - 1}

    for index in range(verb_index - 1, start - 1, -1):
        word, pos = tokens[index]
        if not _is_noun_token(pos):
            continue
        if index + 1 < verb_index:
            particle, particle_pos = tokens[index + 1]
            if particle_pos == 'JX' and particle in ADDITIVE_PARTICLES:
                return _noun_name_at(tokens, index), {index}
        break

    return None, set()


def _find_relation_object(tokens, start, verb_index, base_verb, excluded_indices=None):
    """현재 술어 구간에서 조사로 확인되는 목적어를 우선 선택한다."""
    excluded_indices = set(excluded_indices or [])

    if base_verb == "하다":
        light_object, indices = _find_light_verb_object(tokens, start, verb_index)
        if light_object is not None:
            return light_object, indices

    # '-고 싶다' 구문에서는 이/가가 의미상 목적어를 표시할 수 있다.
    if base_verb == "보고 싶다":
        for index in range(verb_index - 1, start - 1, -1):
            word, pos = tokens[index]
            if (
                index not in excluded_indices
                and _is_noun_token(pos)
                and index + 1 < verb_index
                and tokens[index + 1][1] == 'JKS'
            ):
                noun_name = _noun_name_at(tokens, index)
                return noun_name, _noun_span_indices(tokens, index)

    # 을/를이 붙은 목적어가 가장 강한 근거다.
    for index in range(verb_index - 1, start - 1, -1):
        word, pos = tokens[index]
        if (
            index not in excluded_indices
            and
            _is_noun_token(pos)
            and index + 1 < verb_index
            and tokens[index + 1][1] == 'JKO'
        ):
            noun_name = _noun_name_at(tokens, index)
            used = _noun_span_indices(tokens, index)
            if noun_name in MEASURE_NOUNS:
                for previous in range(index - 1, start - 1, -1):
                    previous_word, previous_pos = tokens[previous]
                    if (
                        previous not in excluded_indices
                        and _is_noun_token(previous_pos)
                        and previous_word not in TEMPORAL_NOUNS
                        and previous_word not in MEASURE_NOUNS
                    ):
                        noun_name = _noun_name_at(tokens, previous)
                        used.add(previous)
                        break
            return noun_name, used

    # `도`는 현재 동사의 사전 프레임보다 앞에서 명시된 격 역할을 우선 잇는다.
    # 따라서 목록에 없는 동사라도 `콩을 넣고 설탕도 넣다`의 설탕을
    # 앞선 콩과 같은 직접목적어로 복원할 수 있다.
    for index in range(verb_index - 1, start - 1, -1):
        _, pos = tokens[index]
        if (
            index not in excluded_indices
            and _is_noun_token(pos)
            and index + 1 < verb_index
            and tokens[index + 1][1] == "JX"
            and tokens[index + 1][0] in ADDITIVE_PARTICLES
            and _infer_additive_role(tokens, index) == "direct_object"
        ):
            return _noun_name_at(tokens, index), _noun_span_indices(tokens, index)

    # '내 편이 되어주다'의 보격 명사는 관계의 의미상 대상으로 사용한다.
    if base_verb == "되어주다":
        for index in range(verb_index - 1, start - 1, -1):
            if (
                index not in excluded_indices
                and _is_noun_token(tokens[index][1])
                and index + 1 < verb_index
                and tokens[index + 1][1] == "JKC"
            ):
                return _noun_name_at(tokens, index), {index}

    # 을/를 없는 약한 목적어 추론에는 동사의 의미역 정보가 필요하다.
    if not _verb_accepts_inferred_direct_object(base_verb):
        return None, set()

    # '얼굴부터 살펴보다'처럼 시작점을 나타내는 보조사가 목적어를 대신한다.
    for index in range(verb_index - 1, start - 1, -1):
        word, pos = tokens[index]
        if (
            index not in excluded_indices
            and _is_noun_token(pos)
            and word not in TEMPORAL_NOUNS
            and word not in QUESTION_GENERIC_NOUNS
            and (
                not _is_likely_person(_noun_name_at(tokens, index))
                or bool(excluded_indices)
            )
            and index + 1 < verb_index
            and tokens[index + 1] == ("부터", "JX")
        ):
            return _noun_name_at(tokens, index), _noun_span_indices(tokens, index)

    # '공부도 잘하다', '딱지도 하다'의 도는 목적격을 보조한다.
    for index in range(verb_index - 1, start - 1, -1):
        word, pos = tokens[index]
        if (
            index in excluded_indices
            or not _is_noun_token(pos)
            or index + 1 >= verb_index
        ):
            continue
        next_word, next_pos = tokens[index + 1]
        if (
            next_pos == 'JX'
            and next_word in ADDITIVE_PARTICLES
            and not ENABLE_ROLE_NEUTRAL_AUXILIARY_PARTICLES
            and word not in TEMPORAL_NOUNS
            and word not in NON_OBJECT_NOUNS
            and (
                not _is_likely_person(_noun_name_at(tokens, index))
                or bool(excluded_indices)
            )
        ):
            return _noun_name_at(tokens, index), _noun_span_indices(tokens, index)

    # 관형절의 '책 읽는 것', 보문절의 '줄 모르다'처럼 무조사 목적어를 보정한다.
    for index in range(verb_index - 1, start - 1, -1):
        word, pos = tokens[index]
        if index in excluded_indices or not _is_noun_token(pos):
            continue
        if excluded_indices and index < max(excluded_indices):
            # 조사 없는 명사가 뒤쪽의 명시 주어보다 앞 절에 있으면
            # 현재 술어의 목적어로 끌고 오지 않는다.
            continue
        if word in NON_OBJECT_NOUNS:
            continue
        if _is_likely_person(_noun_name_at(tokens, index)) and not excluded_indices:
            continue

        next_word = tokens[index + 1][0] if index + 1 < verb_index else None
        next_pos = tokens[index + 1][1] if index + 1 < verb_index else None
        if next_pos == "MAG" and next_word in {"모두", "다"}:
            continue
        if next_pos in {'JKS', 'JKM', 'JKG', 'JC'}:
            continue
        if next_pos == 'JX' and next_word in ADVERBIAL_PARTICLES:
            continue
        if word in TEMPORAL_NOUNS and word not in NOMINALIZER_NOUNS:
            continue
        if next_pos is not None and _is_noun_token(next_pos):
            # '집 앞에서'처럼 뒤의 명사가 구의 중심이면 앞 명사를 목적어로 삼지 않는다.
            continue

        noun_name = _noun_name_at(tokens, index)
        used = _noun_span_indices(tokens, index)
        if noun_name in MEASURE_NOUNS:
            for previous in range(index - 1, start - 1, -1):
                previous_word, previous_pos = tokens[previous]
                if (
                    previous not in excluded_indices
                    and _is_noun_token(previous_pos)
                    and previous_word not in TEMPORAL_NOUNS
                ):
                    noun_name = _noun_name_at(tokens, previous)
                    used.add(previous)
                    break
        return noun_name, used

    return None, set()


def _collect_pending_relative_head_roles(
    tokens,
    relative_head_indices,
    relative_internal_indices,
    verb_index,
    base_verb,
    clause_start=0,
    excluded_indices=None,
    default_subject=None,
    focus_entity=None,
    default_object=None,
    default_object_predicates=None,
    question_required_roles=None,
    relation_history=None,
):
    """관형절 중심어를 조사에 맞는 본절 역할로 복원한다.

    일반 역할 탐색의 시작점보다 앞에 관형절 중심어가 놓이더라도
    주격/목적격/간접격/보어격/부사격 조사를 근거로 본절에 넘긴다.
    접속조사로 이어진 중심어는 오른쪽 명사의 역할을 상속한다.
    """
    roles = {
        "subject": [],
        "direct_object": [],
        "indirect_object": [],
        "object_complement": [],
        "context": [],
    }
    internal_indices = set(relative_internal_indices or [])
    role_excluded_indices = internal_indices | set(excluded_indices or [])

    def particle_role(noun_index):
        noun_span = _noun_span_indices(tokens, noun_index)
        particle_index = max(noun_span) + 1
        if particle_index >= verb_index:
            return None, None, None

        particle_word, particle_pos = tokens[particle_index]
        noun_name = _noun_name_at(tokens, noun_index)
        if (
            ENABLE_ROLE_NEUTRAL_AUXILIARY_PARTICLES
            and particle_pos == "JX"
            and particle_word in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
        ):
            role = _auxiliary_particle_role(
                tokens,
                noun_index,
                clause_start,
                verb_index,
                excluded_indices=role_excluded_indices,
                default_subject=default_subject,
                focus_entity=focus_entity,
                default_object=default_object,
                default_object_predicates=default_object_predicates,
                base_verb=base_verb,
                question_required_roles=question_required_roles,
                relation_history=relation_history,
            )
            return role, particle_index, noun_name
        if (
            particle_pos == "JKS"
            or (
                particle_pos == "JX"
                and particle_word in SUBJECT_TOPIC_PARTICLES | {"이", "가"}
                and noun_name not in TEMPORAL_NOUNS
            )
        ):
            return "subject", particle_index, noun_name
        if (
            particle_pos == "JX"
            and particle_word in SUBJECT_TOPIC_PARTICLES
            and noun_name in TEMPORAL_NOUNS
        ):
            return "context", particle_index, noun_name
        if particle_pos == "JKO":
            return "direct_object", particle_index, noun_name
        if (
            particle_word in INDIRECT_PARTICLES
            and particle_pos.startswith("J")
        ):
            return "indirect_object", particle_index, noun_name
        if (
            particle_word in OBJECT_COMPLEMENT_PARTICLES
            and particle_pos.startswith("J")
            and base_verb in OBJECT_COMPLEMENT_VERBS
        ):
            return "object_complement", particle_index, noun_name
        if particle_pos == "JX" and particle_word in ADDITIVE_PARTICLES:
            inherited_role = _infer_additive_role(tokens, noun_index)
            if inherited_role in roles:
                return inherited_role, particle_index, noun_name
            if noun_name in TEMPORAL_NOUNS:
                return "context", particle_index, noun_name
            if _verb_accepts_inferred_direct_object(base_verb):
                return "direct_object", particle_index, noun_name
            return "subject", particle_index, noun_name
        if (
            particle_pos == "JKM"
            or (
                particle_pos == "JX"
                and particle_word in ADVERBIAL_PARTICLES
            )
        ):
            return "context", particle_index, noun_name
        return None, particle_index, noun_name

    for head_index in sorted(relative_head_indices or []):
        if not (0 <= head_index < verb_index):
            continue
        if not _is_noun_token(tokens[head_index][1]):
            continue

        head_span = _noun_span_indices(tokens, head_index)
        role, particle_index, head_name = particle_role(head_index)
        role_indices = set(head_span)

        # '지키는 습관과 돕는 마음이 기억나다',
        # '고장 난 대문과 의자를 고치다'처럼 접속된 오른쪽 명사의
        # 조사를 찾아 왼쪽 관형절 중심어에도 같은 역할을 준다.
        if (
            particle_index is not None
            and particle_index < verb_index
            and tokens[particle_index][1] == "JC"
        ):
            role_indices.add(particle_index)
            role = None
            for right_index in range(particle_index + 1, verb_index):
                if (
                    right_index in internal_indices
                    or not _is_noun_token(tokens[right_index][1])
                ):
                    continue
                right_role, _, right_name = particle_role(right_index)
                if right_role is None:
                    continue
                if (
                    right_role == "direct_object"
                    and _is_likely_person(head_name)
                    and not _is_likely_person(right_name)
                ):
                    role = "context"
                else:
                    role = right_role
                break
            if role is None:
                role = "context"

        if role is None:
            continue
        if head_name not in {name for _, name, _ in roles[role]}:
            roles[role].append((head_index, head_name, role_indices))

    return roles


def _resolve_role_noun(tokens, index, start):
    """단위명사에 표시된 조사를 실제 대상 명사로 되돌린다."""
    noun_name = _noun_name_at(tokens, index)
    used = _noun_span_indices(tokens, index)
    if noun_name in MEASURE_NOUNS:
        for previous in range(index - 1, start - 1, -1):
            previous_word, previous_pos = tokens[previous]
            if (
                _is_noun_token(previous_pos)
                and previous_word not in TEMPORAL_NOUNS
                and previous_word not in MEASURE_NOUNS
            ):
                noun_name = _noun_name_at(tokens, previous)
                used.add(previous)
                break
    return noun_name, used


def _collect_coordinated_subject_roles(
    tokens,
    start,
    subject_name,
    subject_index,
    excluded_indices=None,
):
    """'논과 산이'처럼 접속조사로 연결된 주어 전체를 수집한다."""
    if subject_index is None:
        return [subject_name], set()

    excluded_indices = set(excluded_indices or [])
    subject_indices = _noun_span_indices(tokens, subject_index)
    if subject_indices & excluded_indices:
        return [subject_name], set()

    # 마지막 명사에 실제 주어 표지가 있을 때만 왼쪽 접속 명사를
    # 같은 주어로 올린다. 따라서 '아버지와 기차를 탔다'의
    # 아버지는 주어 목록에 들어가지 않는다.
    particle_index = max(subject_indices) + 1
    if particle_index >= len(tokens):
        return [subject_name], subject_indices
    particle_word, particle_pos = tokens[particle_index]
    if not (
        particle_pos == "JKS"
        or (
            particle_pos == "JX"
            and particle_word in SUBJECT_TOPIC_PARTICLES
        )
    ):
        return [subject_name], subject_indices

    names_by_index = [(subject_index, subject_name)]
    used_indices = set(subject_indices)
    left_edge = _modified_noun_phrase_left_edge(
        tokens,
        subject_index,
        start,
    )

    while left_edge - 2 >= start:
        connector_index = left_edge - 1
        noun_index = connector_index - 1
        connector_word, connector_pos = tokens[connector_index]
        if connector_pos != "JC" or connector_word not in {"와", "과", "랑", "이랑"}:
            break
        if not _is_noun_token(tokens[noun_index][1]):
            break

        noun_name, role_indices = _resolve_role_noun(tokens, noun_index, start)
        if role_indices & excluded_indices:
            break
        if noun_name not in {name for _, name in names_by_index}:
            names_by_index.append((noun_index, noun_name))
        used_indices.update(role_indices)
        used_indices.add(connector_index)
        left_edge = _modified_noun_phrase_left_edge(
            tokens,
            noun_index,
            start,
        )

    names_by_index.sort(key=lambda item: item[0])
    return [name for _, name in names_by_index], used_indices


def _collect_direct_object_roles(
    tokens,
    start,
    verb_index,
    base_verb,
    primary_name,
    primary_indices,
    excluded_indices=None,
    adnominal_spans=None,
):
    """기존 대표 목적어와 함께 병렬 직접목적어 전체를 수집한다."""
    excluded_indices = set(excluded_indices or [])
    names_by_index = []
    used_indices = set(primary_indices or [])
    explicitly_marked = []

    for index in range(start, verb_index):
        word, pos = tokens[index]
        if index in excluded_indices or not _is_noun_token(pos):
            continue
        if _belongs_to_other_adnominal_clause(
            tokens,
            index,
            verb_index,
            adnominal_spans=adnominal_spans,
        ):
            continue
        if index + 1 >= verb_index:
            continue

        next_word, next_pos = tokens[index + 1]
        is_direct = next_pos == "JKO" or (
            next_pos == "JX"
            and next_word in ADDITIVE_PARTICLES
            and _infer_additive_role(tokens, index) == "direct_object"
        )
        if _verb_accepts_inferred_direct_object(base_verb):
            is_direct = is_direct or (
                next_pos == "JX"
                and next_word in (
                    {"만", "부터"}
                    | (
                        set()
                        if ENABLE_ROLE_NEUTRAL_AUXILIARY_PARTICLES
                        else ADDITIVE_PARTICLES
                    )
                )
                and word not in TEMPORAL_NOUNS
                and word not in QUESTION_GENERIC_NOUNS
                and word not in NON_OBJECT_NOUNS
                and (
                    not _is_likely_person(_noun_name_at(tokens, index))
                    or bool(excluded_indices)
                )
            )
        if not is_direct:
            continue

        noun_name, role_indices = _resolve_role_noun(tokens, index, start)
        if noun_name not in {name for _, name in names_by_index}:
            names_by_index.append((index, noun_name))
        used_indices.update(role_indices)
        explicitly_marked.append(index)

    # '배추와 무를', '생선 한 마리와 두부 한 모를'의 앞쪽 병렬 목적어.
    if explicitly_marked:
        for index in range(start, verb_index - 1):
            word, pos = tokens[index]
            if index in excluded_indices or not _is_noun_token(pos):
                continue
            if tokens[index + 1][1] != "JC":
                continue

            right_markers = [marker for marker in explicitly_marked if marker > index]
            if not right_markers:
                continue
            nearest_marker = min(right_markers)
            has_intervening_subject = any(
                token_pos == "JKS"
                or (
                    token_pos == "JX"
                    and token_word in SUBJECT_TOPIC_PARTICLES
                )
                for token_word, token_pos in tokens[index + 2:nearest_marker]
            )
            if has_intervening_subject:
                continue

            noun_name, role_indices = _resolve_role_noun(tokens, index, start)
            nearest_name, _ = _resolve_role_noun(
                tokens,
                nearest_marker,
                start,
            )
            # '어머니와 송편을 빚다'의 사람 명사는 동반자다.
            # '철수와 영희를 만나다'처럼 양쪽이 모두 사람이면 병렬
            # 직접목적어로 유지한다.
            if (
                _is_likely_person(noun_name)
                and not _is_likely_person(nearest_name)
            ):
                continue
            if noun_name not in {name for _, name in names_by_index}:
                names_by_index.append((index, noun_name))
            used_indices.update(role_indices)

    names_by_index.sort(key=lambda item: item[0])
    direct_names = [name for _, name in names_by_index]
    if primary_name is not None and primary_name not in direct_names:
        direct_names.append(primary_name)
    return direct_names, used_indices


def _collect_indirect_object_roles(
    tokens,
    start,
    verb_index,
    base_verb,
    subject,
    default_subject,
    default_indirect_object=None,
    excluded_indices=None,
    allow_default_inference=True,
):
    """에게/한테/께와 수여·수혜 동사 문형으로 간접목적어를 수집한다."""
    excluded_indices = set(excluded_indices or [])
    names = []
    used_indices = set()

    for index in range(start, verb_index):
        word, pos = tokens[index]
        if (
            index in excluded_indices
            or not _is_noun_token(pos)
            or index + 1 >= verb_index
        ):
            continue
        particle_word, particle_pos = tokens[index + 1]
        if (
            particle_word in INDIRECT_PARTICLES
            and particle_pos.startswith("J")
            and word not in TEMPORAL_NOUNS
        ):
            noun_name, role_indices = _resolve_role_noun(tokens, index, start)
            if noun_name != subject.noun and noun_name not in names:
                names.append(noun_name)
                used_indices.update(role_indices)

    if not names and _is_beneficiary_verb(base_verb):
        if (
            default_indirect_object is not None
            and default_indirect_object.noun != subject.noun
            and (
                allow_default_inference
                or subject.noun == default_subject.noun
            )
        ):
            names.append(default_indirect_object.noun)
        elif allow_default_inference and subject.noun != default_subject.noun:
            names.append(default_subject.noun)

    return names, used_indices


def _collect_object_complement_roles(
    tokens,
    start,
    verb_index,
    base_verb,
    excluded_indices=None,
):
    """'철수를 반장으로 뽑다'의 반장처럼 목적어를 설명하는 보어를 찾는다."""
    if base_verb not in OBJECT_COMPLEMENT_VERBS:
        return [], set()

    excluded_indices = set(excluded_indices or [])
    names = []
    used_indices = set()
    for index in range(start, verb_index):
        word, pos = tokens[index]
        if (
            index in excluded_indices
            or not _is_noun_token(pos)
            or index + 1 >= verb_index
        ):
            continue
        particle_word, particle_pos = tokens[index + 1]
        if (
            particle_word in OBJECT_COMPLEMENT_PARTICLES
            and particle_pos.startswith("J")
        ):
            noun_name, role_indices = _resolve_role_noun(tokens, index, start)
            if noun_name not in names:
                names.append(noun_name)
                used_indices.update(role_indices)

    return names, used_indices


def _collect_relation_context(tokens, start, end, used_indices):
    """주어/목적어가 아닌 시간·장소·동반자 명사만 문맥으로 보존한다."""
    context = []
    skip_indices = set(used_indices)

    for index in range(start, end):
        if index in skip_indices:
            continue

        word, pos = tokens[index]
        if not _is_noun_token(pos):
            continue

        # '친/구들'은 하나의 명사다.
        if (
            word == "친"
            and index + 1 < end
            and tokens[index + 1][0] == "구들"
        ):
            continue

        noun_name = _noun_name_at(tokens, index)
        if noun_name in MEASURE_NOUNS:
            for previous in range(index - 1, start - 1, -1):
                previous_word, previous_pos = tokens[previous]
                if (
                    _is_noun_token(previous_pos)
                    and previous_word not in TEMPORAL_NOUNS
                    and previous_word not in MEASURE_NOUNS
                ):
                    noun_name = _noun_name_at(tokens, previous)
                    skip_indices.add(previous)
                    break
        next_word = tokens[index + 1][0] if index + 1 < end else None
        next_pos = tokens[index + 1][1] if index + 1 < end else None

        # `하나도 버리지 않았다`의 하나도는 대상·장소가 아니라 부정의
        # 수량 범위를 나타낸다. 앞 명사로 되돌려 context에 중복하지 않는다.
        if (
            word in MEASURE_NOUNS
            and next_pos == "JX"
            and next_word in ADDITIVE_PARTICLES
        ):
            continue

        if word in positions and index > start and _is_noun_token(tokens[index - 1][1]):
            noun_name = f"{tokens[index - 1][0]} {word}"
            skip_indices.add(index - 1)

        is_context = (
            next_pos in {'JKM', 'JC'}
            or (next_pos == 'JX' and next_word in ADVERBIAL_PARTICLES | ADDITIVE_PARTICLES)
            or (
                next_pos == 'JX'
                and next_word in SUBJECT_TOPIC_PARTICLES
                and (
                    word in TEMPORAL_NOUNS
                    or noun_name in positions
                    or (index > start and tokens[index - 1][1] == 'JC')
                )
            )
            or (index + 1 < end and tokens[index + 1] == ("없이", "MAG"))
        )
        if is_context and noun_name not in context:
            context.append(noun_name)

    return context


def _predicate_has_reason_result(tokens, predicate_index):
    """현재 술어 뒤가 -아서/-어서 이유절이고 감정·평가 결과가 이어지는지 본다."""
    connective_index = predicate_index + 1
    if connective_index >= len(tokens):
        return False
    connective_word, connective_pos = tokens[connective_index]
    if (
        not connective_pos.startswith("EC")
        or connective_word not in REASON_CONNECTIVE_ENDINGS
    ):
        return False

    for word, pos in tokens[connective_index + 1:]:
        if pos == "SF":
            break
        if pos in {"VA", "XSA"} and word in REASON_RESULT_STEMS:
            return True
        # 이유 결과를 확인하기 전에 독립 동사가 나오면 일반적인 순차 연결로 본다.
        if pos in {"VV", "VXV", "XSV"}:
            break
    return False


def _history_confirms_direct_object(relations, verb, object_name):
    """이전 관계에서 같은 동사와 직접목적어의 연결이 있었는지 확인한다.

    질문 중심어를 관형절에 넘길 때 동사 사전을 대신하는 근거로 사용한다.
    주어와 context는 확인 대상에서 제외하여 중심어가 주어로 역전되지 않게
    하고, 이유절 안에 저장된 관계도 이전의 명시적 연결로 인정한다.
    """
    pending = list(relations or [])
    while pending:
        relation = pending.pop()
        if relation.verb == verb and any(
            item.noun == object_name
            for item in getattr(relation, "direct_objects", [])
        ):
            return True
        pending.extend(getattr(relation, "reason_clauses", []))
    return False


def _adnominal_predicate_match_keys(tokens, predicate_index):
    """관형 술어를 Q와 A 사이에서 비교할 형태 기반 후보를 만든다.

    형태소 분석기가 `빈`을 Q에서는 빌/VV, A에서는 비/VV로 다르게
    복원하는 경우가 있어 ETD 앞 어간의 ㄹ 종성 탈락 후보도 함께 둔다.
    단어 목록을 사용하지 않고 한글 활용 형태만 정규화한다.
    """
    base = _normalize_relation_verb(tokens, predicate_index)
    keys = {base}
    if not base.endswith("다") or len(base) < 2:
        return frozenset(keys)

    stem = base[:-1]
    last = stem[-1]
    syllable_offset = ord(last) - 0xAC00
    if 0 <= syllable_offset <= 0xD7A3 - 0xAC00 and syllable_offset % 28 == 8:
        open_syllable = chr(ord(last) - 8)
        keys.add(stem[:-1] + open_syllable + "다")
    return frozenset(keys)


def _matching_question_adnominal_patterns(tokens, span, patterns):
    """A의 관형 술어·중심 명사와 대응하는 Q 관형 패턴을 반환한다."""
    if not patterns:
        return []
    predicate_index = span["predicate"]
    _, head_index = _find_relative_head(tokens, predicate_index)
    if head_index is None:
        return []
    head_token = tokens[head_index][0]
    predicate_keys = _adnominal_predicate_match_keys(tokens, predicate_index)
    return [
        pattern
        for pattern in patterns
        if pattern.get("head_token") == head_token
        and predicate_keys.intersection(pattern.get("predicate_keys", ()))
    ]


def _mark_question_confirmed_adnominal_spans(tokens, spans, patterns):
    """Q와 같은 주어까지 확인된 관형 범위를 본절 역할 탐색에서 가린다."""
    for span in spans:
        matching_patterns = _matching_question_adnominal_patterns(
            tokens,
            span,
            patterns,
        )
        if not matching_patterns:
            continue
        subject_name, _ = _find_explicit_subject(
            tokens,
            span["start"],
            span["predicate"],
        )
        if subject_name is not None and any(
            pattern.get("subject") == subject_name
            for pattern in matching_patterns
        ):
            span["mask_after_completion"] = True
    return spans


def _adnominal_form_prefers_head_subject(tokens, predicate_index):
    """어휘 사전 없이 중심 명사의 상태 변화형임을 확인한다."""
    return any(
        key.endswith(("아지다", "어지다", "여지다", "해지다"))
        for key in _adnominal_predicate_match_keys(tokens, predicate_index)
    )


def _qa_common_modifier_can_reserve_main_subject(
    tokens,
    subject_index,
    predicate_index=None,
):
    """Q/A 공통 관형표현 앞의 본절 주어 후보인지 확인한다.

    이/가는 받침에 따른 동일 조사의 이형태이므로 구분 근거로 사용하지
    않는다. 공통 관형표현의 중심어가 본절의 장소·간접 문맥이고 그 뒤에
    별도 직접목적어가 있는 구조에서만 앞 주격 명사를 본절 주어로 남긴다.
    따라서 `직원이 [빈 밥그릇에] 사료를 담다`는 보존하지만, 목적어를
    꾸미는 `친구가 [잡은 물고기를] 굽다`의 친구까지 승격하지 않는다.
    """
    if (
        subject_index is None
        or not _is_noun_token(tokens[subject_index][1])
    ):
        return False
    subject_particle_index = subject_index + 1
    if subject_particle_index >= len(tokens):
        return False
    subject_particle_word, subject_particle_pos = tokens[subject_particle_index]
    if not (
        subject_particle_pos == "JKS"
        or (
            subject_particle_pos == "JX"
            and subject_particle_word in {"이", "가"}
        )
    ):
        return False
    if predicate_index is None:
        return False
    if any(
        tokens[index][1] in {"VV", "VXV", "XSV", "VA", "XSA"}
        and _verb_has_following_etd(tokens, index)
        for index in range(subject_index + 1, predicate_index)
    ):
        return False

    _, head_index = _find_relative_head(tokens, predicate_index)
    if head_index is None:
        return False
    head_end = _noun_phrase_right_edge(tokens, head_index)
    head_particle_index = head_end + 1
    if head_particle_index >= len(tokens):
        return False
    head_particle_word, head_particle_pos = tokens[head_particle_index]
    is_adverbial_head = (
        head_particle_pos in {"JKM", "JKB"}
        or head_particle_word in ADVERBIAL_PARTICLES
    )
    if not is_adverbial_head:
        return False

    return any(
        tokens[index][1] == "JKO"
        for index in range(head_particle_index + 1, len(tokens))
    )


def _question_guided_main_subject_indices(
    tokens,
    adnominal_spans,
    question_required_roles=None,
    question_adnominal_patterns=None,
):
    """관형절 주어를 제외하고 A의 본절 주어 후보를 보호한다.

    뒤쪽에 별도 본절 주어가 있거나 관형 중심 명사가 본절 주어·주제로
    표시된 경우에는 앞 주어를 관형절 소속으로 확정한다. 그 외의 단일
    앞 주어는 관형절이 임의로 소비하지 못하게 본절 후보로 보존한다.
    Q가 주어 답을 요구하면 이 보존 후보를 가장 강하게 사용한다.
    """
    question_requires_subject = (
        "subject" in set(question_required_roles or [])
    )

    predicate_positions = {"VV", "VXV", "XSV", "VA", "XSA", "VCP"}
    main_predicate_candidates = [
        index
        for index, (_, pos) in enumerate(tokens)
        if pos in predicate_positions and not _verb_has_following_etd(tokens, index)
    ]
    if not main_predicate_candidates:
        return set()
    main_predicate_index = main_predicate_candidates[-1]

    adnominal_indices = {
        index
        for span in adnominal_spans
        for index in range(span["start"], span["end"] + 1)
    }
    # 시간·조건·이유절의 주어는 본절 후보에서 제외한다. 관형절 범위는
    # 아래에서 우선순위를 정하는 데 따로 사용하므로 여기서는 다시 뺀다.
    subordinate_indices = (
        _question_non_main_clause_indices(tokens) - adnominal_indices
    )

    candidates = []
    for index in range(main_predicate_index):
        if index in subordinate_indices or not _is_noun_token(tokens[index][1]):
            continue
        # 역할 위치는 기존 `_find_explicit_subject()`와 동일하게 조사 바로
        # 앞의 마지막 명사 토큰으로 통일한다.
        particle_index = index + 1
        if particle_index >= main_predicate_index:
            continue
        particle_word, particle_pos = tokens[particle_index]
        is_subject = (
            particle_pos == "JKS"
            or (particle_pos == "JX" and particle_word in {"이", "가"})
            or (
                particle_pos == "JX"
                and particle_word in SUBJECT_TOPIC_PARTICLES
                and _noun_name_at(tokens, index) not in TEMPORAL_NOUNS
            )
            or (
                particle_pos == "JX"
                and particle_word in ADDITIVE_PARTICLES
                and _infer_additive_role(tokens, index) == "subject"
            )
        )
        if is_subject:
            candidates.append(index)

    if not candidates:
        return set()

    question_reserved_subjects = set()
    question_confirmed_relative_subjects = set()
    for span in adnominal_spans:
        matching_patterns = _matching_question_adnominal_patterns(
            tokens,
            span,
            question_adnominal_patterns,
        )
        if not matching_patterns:
            continue

        span_subjects = [
            index
            for index in candidates
            if span["start"] <= index < span["predicate"]
        ]
        if not span_subjects:
            continue

        _, head_index = _find_relative_head(tokens, span["predicate"])
        if head_index is None:
            continue
        head_end = _noun_phrase_right_edge(tokens, head_index)
        later_main_subject_exists = any(
            head_end < candidate < main_predicate_index
            for candidate in candidates
        )

        for subject_index in span_subjects:
            subject_name = _noun_name_at(tokens, subject_index)
            if any(
                pattern.get("subject") == subject_name
                for pattern in matching_patterns
            ):
                # Q에도 같은 주어가 포함된 관형 범위라면 A에서도 관형절
                # 내부 주어로 확정한다: `파도가 높은 날`.
                question_confirmed_relative_subjects.add(subject_index)
            elif (
                any(
                    pattern.get("subject") is None
                    for pattern in matching_patterns
                )
                and not later_main_subject_exists
                and (
                    (
                        ENABLE_QA_COMMON_ADNOMINAL_MODIFIER
                        and _qa_common_modifier_can_reserve_main_subject(
                            tokens,
                            subject_index,
                            span["predicate"],
                        )
                    )
                    or
                    ENABLE_QUESTION_SUBJECTLESS_ADNOMINAL_MAIN_GUARD
                    or question_requires_subject
                    or _adnominal_form_prefers_head_subject(
                        tokens,
                        span["predicate"],
                    )
                )
            ):
                # Q의 대응 관형 범위가 무주어인데 A 앞쪽에 새 주어가
                # 추가됐다면 본절 주어로 보호한다: `어부가 찢어진 그물`.
                question_reserved_subjects.add(subject_index)

    confirmed_relative_subjects = set(question_confirmed_relative_subjects)
    for span in adnominal_spans:
        span_subjects = [
            index
            for index in candidates
            if span["start"] <= index < span["predicate"]
        ]
        if not span_subjects:
            continue

        _, head_index = _find_relative_head(tokens, span["predicate"])
        if head_index is None:
            continue
        head_name = _noun_name_at(tokens, head_index)
        head_end = _noun_phrase_right_edge(tokens, head_index)
        particle_index = head_end + 1
        head_particle = (
            tokens[particle_index]
            if particle_index < len(tokens)
            else (None, None)
        )
        head_particle_word, head_particle_pos = head_particle
        head_is_main_subject = (
            head_particle_pos == "JKS"
            or (
                head_particle_pos == "JX"
                and head_particle_word
                in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
            )
        )
        later_main_subject_exists = any(
            head_end < candidate < main_predicate_index
            for candidate in candidates
        )
        head_has_non_argument_role = (
            head_name in TEMPORAL_NOUNS
            or head_name in RELATIVE_CONTENT_NOUNS
        )
        if (
            later_main_subject_exists
            or head_is_main_subject
            or head_has_non_argument_role
        ):
            confirmed_relative_subjects.update(span_subjects)

    outside_adnominal = [
        index
        for index in candidates
        if (
            index not in adnominal_indices
            and index not in confirmed_relative_subjects
        )
    ]
    if outside_adnominal:
        # 본절 동사에 가장 가까운 관형절 밖 주어를 사용한다.
        return {outside_adnominal[-1]}

    if question_reserved_subjects:
        # Q-A 관형 범위 대응은 단순 위치 추정보다 강한 근거다.
        return {max(question_reserved_subjects)}

    ambiguous_main_candidates = [
        index
        for index in candidates
        if index not in confirmed_relative_subjects
    ]
    if question_requires_subject and ambiguous_main_candidates:
        # Q가 본절 주어를 요구할 때만 아직 관형절 소속으로 확정되지
        # 않은 첫 주어를 보호한다. 일반 답변의 단일 앞 주어까지 항상
        # 보호하면 `준호가 추천한 책을 빌리다`의 준호가 빌린 사람으로
        # 바뀌므로, Q 근거가 없는 모호한 경우에는 기존 범위를 유지한다.
        return {ambiguous_main_candidates[0]}

    if (
        ENABLE_DEFERRED_SUBJECT_CANDIDATE_ALLOCATION
        and ambiguous_main_candidates
    ):
        # 실험 정책: 명시적으로 관형절에 확정되지 않은 후보 하나를 먼저
        # 본절에 배정한다. 다른 후보는 각자의 가장 가까운 관형절 범위에
        # 남으므로 `철수가 영희가 만든 음식`에서는 철수/영희가 분리된다.
        return {ambiguous_main_candidates[0]}

    # 모든 명시 주어가 관형절 소속이거나 Q가 본절 주어를 요구하지
    # 않으면 생략 주어는 이후의 focus/default 복원에 맡긴다.
    return set()


def generate_relations(
    tokens,
    default_subject,
    noun_dict,
    focus_entity=None,
    default_object=None,
    default_object_predicates=None,
    default_indirect_object=None,
    remembered_focus_entity=None,
    relation_history=None,
    question_required_roles=None,
    question_adnominal_patterns=None,
):
    """조사, 절 경계, 관형절을 기준으로 S-V-O 관계를 생성한다."""
    relations = []
    default_subject_obj = default_subject or _get_relation_noun(noun_dict, "나")
    # Q의 주어 후보는 A 본절의 빈 주어만 채운다. 관형절 내부의 생략
    # 주어까지 같은 중심어로 채우지 않도록 화자 fallback과 분리한다.
    answer_subject_obj = focus_entity or default_subject_obj
    fallback_subject_obj = default_subject_obj
    discourse_subject = answer_subject_obj
    sentence_subject = answer_subject_obj
    sentence_start = 0
    predicate_start = 0
    pending_relative_indices = set()
    pending_relative_head_indices = set()
    pending_reason_clauses = []
    # 형용사 관형형은 관계 생성 루프에서 건너뛰므로 기존의
    # pending_relative_indices에 들어가지 않았다. 미리 계산한 동일 범위를
    # 뒤 술어의 역할 탐색에서 제외해 관형절 내부 주어의 누출을 막는다.
    reserved_main_object_indices = _question_confirmed_main_object_indices(
        tokens,
        default_object,
        question_required_roles=question_required_roles,
    )
    raw_adnominal_spans = _adnominal_clause_spans(
        tokens,
        reserved_main_role_indices=reserved_main_object_indices,
    )
    reserved_main_subject_indices = _question_guided_main_subject_indices(
        tokens,
        raw_adnominal_spans,
        question_required_roles=question_required_roles,
        question_adnominal_patterns=question_adnominal_patterns,
    )
    adnominal_spans = _adnominal_clause_spans(
        tokens,
        reserved_main_subject_indices=reserved_main_subject_indices,
        reserved_main_role_indices=reserved_main_object_indices,
    )
    adnominal_spans = _mark_question_confirmed_adnominal_spans(
        tokens,
        adnominal_spans,
        question_adnominal_patterns,
    )
    # 한 문장 안에서 연결어미로 이어지는 바로 다음 술어에만 전달한다.
    # Q/A 중심어를 보존하는 discourse_subject와 섞지 않는다.
    connected_clause_state = None

    # 형태소 분석기가 분리한 일부 합성동사를 한 술어로 합친다.
    compound_starts = {}
    compound_ends = {}
    compound_core_ends = {}
    compound_role_exclusions = {}
    compound_map = {
        ("뛰", "어", "다니"): "뛰어다니다",
        ("하", "어", "주"): "해주다",
        ("챙기", "어", "주"): "챙겨주다",
        ("깎", "아", "주"): "깎아주다",
        ("싸", "아", "주"): "싸주다",
        ("만들", "어", "주"): "만들어주다",
        ("나누", "어", "주"): "나눠주다",
        ("나누", "어", "먹"): "나눠먹다",
        ("기다리", "어", "지"): "기다려지다",
        ("모이", "어", "있"): "모여있다",
        ("고치", "어", "주"): "고쳐주다",
        ("들리", "어", "드리"): "들려드리다",
        ("안", "아", "주"): "안아주다",
        ("안", "아", "보"): "안아보다",
        ("사", "아", "먹"): "사먹다",
        ("싸", "아", "가"): "싸가다",
        ("줍", "어", "오"): "주워오다",
        ("되", "어", "주"): "되어주다",
    }
    for index in range(len(tokens) - 2):
        first_word, first_pos = tokens[index]
        link_word, link_pos = tokens[index + 1]
        last_word, last_pos = tokens[index + 2]
        key = (first_word, link_word, last_word)
        compound_verb = None
        if (
            first_pos in {'VV', 'VXV'}
            and link_pos.startswith('EC')
            and last_pos in {'VV', 'VXV', 'VX'}
            and key in compound_map
        ):
            compound_verb = compound_map[key]
        elif (
            first_pos in {'VV', 'VXV', 'XSV', 'VA', 'XSA'}
            and link_pos.startswith('EC')
            and last_pos in {'VXV', 'VXA', 'VX'}
            and link_word in AUXILIARY_CONNECTIVE_RULES.get(last_word, set())
        ):
            compound_verb = _compose_auxiliary_predicate(
                tokens,
                index,
                link_word,
                last_word,
                compound_override=compound_ends.get(index),
            )

        if compound_verb is not None:
            end_index = index + 2
            core_verb = compound_core_ends.get(index)
            if core_verb is None:
                core_verb = _normalize_relation_verb(tokens, index)
            compound_starts[index] = end_index
            compound_ends[end_index] = compound_verb
            compound_core_ends[end_index] = core_verb
            core_frame = VERB_ARGUMENT_FRAMES.get(core_verb)
            if core_frame is not None:
                # 보조용언은 상·양태·수혜 의미만 더하므로 앞 기본동사의
                # 직접목적어 수용 여부를 그대로 유지한다.
                VERB_ARGUMENT_FRAMES.setdefault(compound_verb, core_frame)
            excluded = set(compound_role_exclusions.get(index, set()))
            if (
                first_pos in {'XSV', 'XSA'}
                and index > 0
                and tokens[index - 1][1] in {'NNG', 'NNP', 'XR'}
            ):
                excluded.add(index - 1)
            compound_role_exclusions[end_index] = excluded

    index = 0
    while index < len(tokens):
        word, pos = tokens[index]

        if pos == 'SF':
            if pending_reason_clauses:
                # 뒤에 붙일 본절이 없으면 이유절 관계 자체는 버리지 않는다.
                relations.extend(pending_reason_clauses)
                pending_reason_clauses = []
            sentence_start = index + 1
            predicate_start = index + 1
            pending_relative_indices = set()
            pending_relative_head_indices = set()
            connected_clause_state = None
            if sentence_subject is not None and _is_likely_person(sentence_subject.noun):
                discourse_subject = sentence_subject
            else:
                discourse_subject = answer_subject_obj
            index += 1
            continue

        if index in compound_starts:
            # 두 번째 동사에서 하나의 관계로 생성한다.
            index += 1
            continue

        # 형용사/형용사 파생 접미사는 관계를 만들지 않지만 절 경계는 갱신한다.
        # 그렇지 않으면 '햇볕이 뜨거워서 ... 참고'에서 햇볕이 참다의
        # 주어로 새어 들어간다.
        if pos in {'VA', 'XSA'}:
            # '곱게 차려입다', '넓게 펴다', '즐겁게 하다'의 -게형은
            # 뒤 동사를 꾸미므로 앞 목적어를 버리지 않는다.
            # '관객들이 큰 박수를', '하얀 저고리와 검은 치마를'의
            # 관형형 형용사도 동일 절 안의 주어·목적어 범위를 끊지 않는다.
            if not (
                (
                    index + 1 < len(tokens)
                    and tokens[index + 1][0] == "게"
                    and tokens[index + 1][1].startswith('EC')
                )
                or _verb_has_following_etd(tokens, index)
            ):
                predicate_start = index + 1
            index += 1
            continue

        if pos == 'XSV':
            raw_stem = tokens[index - 1][0] if index > 0 else None
            nominal_stem = (
                raw_stem
                if index > 0 and _is_noun_token(tokens[index - 1][1])
                else None
            )
            jko_bounded_adjective = (
                raw_stem not in ADJECTIVE_HADA_ROOTS
                and _is_jko_bounded_adjective_predicate(
                    tokens,
                    index,
                    predicate_start,
                )
            )
            if (
                word == "하"
                and not _is_adjective_predicate(tokens, index)
                and not jko_bounded_adjective
            ):
                pass
            else:
                # 이번 단계에서는 `명사+하다`만 일반화한다. `명사+되다` 등은
                # 동사 복원 규칙이 별도로 필요하므로 기존처럼 건너뛴다.
                # 형태소 분석기가 XSV로 잘못 준 `고소하다` 등의 형용사도
                # 형용사 처리 단계에 맡긴다. 관형형이면 VA와 마찬가지로
                # 앞의 본절 목적어 범위를 유지해야 `부품들을 투명한
                # 상자에 모으다`의 부품들이 사라지지 않는다.
                if not jko_bounded_adjective:
                    predicate_start = index + 1
                index += 1
                continue

        if pos not in {'VV', 'VXV', 'XSV'} and index not in compound_ends:
            index += 1
            continue

        # 독립 의미가 없는 보조용언/담화 연결 표현은 관계로 만들지 않는다.
        if pos in {'VXV', 'VXA', 'VX'} and index not in compound_ends:
            predicate_start = index + 1
            index += 1
            continue
        if (
            (word == "그러" and index + 1 < len(tokens) and tokens[index + 1][1] == 'ETN')
            or word == "그리하"
        ):
            predicate_start = index + 1
            index += 1
            continue
        if word in {"어렵", "어리"} and _verb_has_following_etd(tokens, index):
            predicate_start = index + 1
            index += 1
            continue
        if (
            word == "좋"
            and index + 1 < len(tokens)
            and tokens[index + 1][1].startswith('EC')
        ):
            predicate_start = index + 1
            index += 1
            continue
        if (
            index + 2 < len(tokens)
            and tokens[index + 1] == ("기", "ETN")
            and tokens[index + 2][1] == 'JKO'
            and any(
                later_word == "하" and later_pos == "VV"
                for later_word, later_pos in tokens[index + 3:]
            )
        ):
            # '구슬치기를 하다'에서 치다를 독립 술어로 중복 생성하지 않는다.
            index += 1
            continue
        if (
            word == "달라"
            and index + 1 < len(tokens)
            and tokens[index + 1][0] == "고"
            and tokens[index + 1][1].startswith('EC')
        ):
            predicate_start = index + 1
            index += 1
            continue
        if (
            word == "하"
            and index >= 3
            and tokens[index - 1][1] == 'JKS'
            and tokens[index - 2][1] == 'ETN'
            and tokens[index - 3][1] == 'VA'
        ):
            predicate_start = index + 1
            index += 1
            continue

        # 수관형사 '한 삼십 분'을 KKMA가 하/VV+ㄴ/ETD로 오인하는 경우 제외.
        if (
            word == "하"
            and index + 2 < len(tokens)
            and tokens[index + 1][1] == 'ETD'
            and (
                tokens[index + 2][1] in {'NR', 'NNM', 'NNB'}
                or tokens[index + 2][0] in MEASURE_NOUNS
            )
        ):
            index += 1
            continue

        completed_adnominal_indices = _completed_adnominal_indices(
            adnominal_spans,
            index,
        )
        explicit_subject_name, subject_index = _find_explicit_subject(
            tokens,
            predicate_start,
            index,
            excluded_indices=(
                set(pending_relative_indices)
                | completed_adnominal_indices
            ),
        )

        base_verb = _normalize_relation_verb(
            tokens,
            index,
            explicit_subject_name=explicit_subject_name,
            compound_verb=compound_ends.get(index),
        )
        inherited_clause_state = (
            connected_clause_state
            if ENABLE_CONNECTED_CLAUSE_SHARING
            else None
        )
        connected_clause_state = None
        subject_inherited_from_connector = False
        object_inherited_from_connector = False
        is_reason_clause = _predicate_has_reason_result(tokens, index)
        derivational_stem_indices = (
            {index - 1}
            if (
                pos == "XSV"
                and index > 0
                and _is_noun_token(tokens[index - 1][1])
            )
            else set()
        )

        # '보고 싶다'의 이/가는 목적어 표지이므로 일반 주어 판정을 취소한다.
        if base_verb == "보고 싶다":
            explicit_subject_name = None
            subject_index = None

        relative_head_name, relative_head_index = _find_relative_head(tokens, index)
        is_relative_clause = relative_head_name is not None
        question_topic_object_reclassified = False
        if (
            not is_relative_clause
            and _question_topic_prefers_object(
                tokens,
                subject_index,
                explicit_subject_name,
                index,
                base_verb,
                default_object,
                default_object_predicates,
                question_required_roles=question_required_roles,
            )
        ):
            explicit_subject_name = None
            subject_index = None
            question_topic_object_reclassified = True
        # 형태소 분석기가 `가져온`을 `가/VV+아/EC+가져오/VV+ㄴ/ETD`처럼
        # 여러 술어로 나누더라도, ETD까지의 모든 술어 성분은 하나의
        # 관형절 주어 영역에 둔다. 이 영역의 주어·연결 상태는 본절 상태를
        # 갱신하지 않는다.
        adnominal_scope = next(
            (
                span
                for span in adnominal_spans
                if span["start"] <= index <= span["end"]
            ),
            None,
        )
        in_adnominal_scope = adnominal_scope is not None
        is_adnominal_internal_predicate = (
            in_adnominal_scope and not is_relative_clause
        )
        relative_head_is_object_complement = (
            is_relative_clause
            and _adnominal_object_complement_start(
                tokens,
                predicate_start,
                index,
                relative_head_index,
            )
            is not None
        )
        relative_subject_unresolved = False
        relative_subject_from_head = False
        relative_outer_subject_excluded = False
        relative_has_explicit_object = False
        relative_has_object_candidate = False
        relative_bare_object_name = None
        relative_bare_object_index = None
        pending_head_roles = {
            "subject": [],
            "direct_object": [],
            "indirect_object": [],
            "object_complement": [],
            "context": [],
        }
        pending_subject_applies = False

        if is_relative_clause:
            outer_explicit_subject_name = explicit_subject_name
            outer_subject_index = subject_index
            role_start = _find_relative_clause_start(
                tokens,
                predicate_start,
                index,
                base_verb,
                relative_head_index=relative_head_index,
                reserved_main_subject_indices=reserved_main_subject_indices,
                reserved_main_role_indices=reserved_main_object_indices,
            )
            role_excluded_indices = (
                set(pending_relative_indices)
                | set(pending_relative_head_indices)
                | completed_adnominal_indices
                | derivational_stem_indices
                | set(compound_role_exclusions.get(index, set()))
            )
            explicit_subject_name, subject_index = _find_explicit_subject(
                tokens,
                role_start,
                index,
                excluded_indices=role_excluded_indices,
            )
            relative_outer_subject_excluded = (
                outer_explicit_subject_name is not None
                and outer_subject_index is not None
                and outer_subject_index < role_start
                and explicit_subject_name is None
            )
            base_verb = _normalize_relation_verb(
                tokens,
                index,
                explicit_subject_name=explicit_subject_name,
                compound_verb=(
                    compound_ends.get(index)
                ),
            )
            relative_internal_indices = {
                noun_index
                for noun_index in range(role_start, index)
                if (
                    noun_index not in role_excluded_indices
                    and _is_noun_token(tokens[noun_index][1])
                )
            }
            relative_has_explicit_object = _has_explicit_direct_object(
                tokens,
                role_start,
                index,
            )
            probed_object_name, _ = _find_relation_object(
                tokens,
                role_start,
                index,
                base_verb,
                excluded_indices=role_excluded_indices,
            )
            relative_has_object_candidate = probed_object_name is not None
            if (
                explicit_subject_name is None
                and index > role_start
                and index - 1 not in role_excluded_indices
                and _is_noun_token(tokens[index - 1][1])
                and _noun_name_at(tokens, index - 1) not in (
                    TEMPORAL_NOUNS | QUESTION_GENERIC_NOUNS | NON_OBJECT_NOUNS
                )
                and (
                    relative_head_name not in RELATIVE_CONTENT_NOUNS
                    or relative_head_name in NOMINALIZER_NOUNS
                )
                and relative_head_name not in TEMPORAL_NOUNS
                and base_verb not in BARE_SUBJECT_VERBS
                and not (
                    base_verb in {"생각나다", "기억나다", "고장나다"}
                    and tokens[index - 1][0] in {"생각", "기억", "고장"}
                )
            ):
                relative_bare_object_name = _noun_name_at(tokens, index - 1)
                relative_bare_object_index = index - 1
                relative_has_object_candidate = True
        else:
            role_start = predicate_start
            role_excluded_indices = (
                set(pending_relative_indices)
                | completed_adnominal_indices
                | derivational_stem_indices
                | set(compound_role_exclusions.get(index, set()))
            )
            relative_internal_indices = set()
            if pending_relative_head_indices:
                pending_head_roles = _collect_pending_relative_head_roles(
                    tokens,
                    pending_relative_head_indices,
                    pending_relative_indices,
                    index,
                    base_verb,
                    clause_start=sentence_start,
                    excluded_indices=role_excluded_indices,
                    default_subject=default_subject_obj,
                    focus_entity=focus_entity,
                    default_object=default_object,
                    default_object_predicates=default_object_predicates,
                    question_required_roles=question_required_roles,
                    relation_history=(
                        list(relation_history or []) + relations
                    ),
                )
                reclassified_pending_topics = [
                    entry
                    for entry in pending_head_roles["subject"]
                    if _question_topic_prefers_object(
                        tokens,
                        entry[0],
                        entry[1],
                        index,
                        base_verb,
                        default_object,
                        default_object_predicates,
                        question_required_roles=question_required_roles,
                    )
                ]
                if reclassified_pending_topics:
                    pending_head_roles["subject"] = [
                        entry
                        for entry in pending_head_roles["subject"]
                        if entry not in reclassified_pending_topics
                    ]
                    for entry in reclassified_pending_topics:
                        if entry[1] not in {
                            name
                            for _, name, _ in pending_head_roles["direct_object"]
                        }:
                            pending_head_roles["direct_object"].append(entry)
                    if explicit_subject_name in {
                        entry[1] for entry in reclassified_pending_topics
                    }:
                        explicit_subject_name = None
                        subject_index = None
                    question_topic_object_reclassified = True
                if (
                    pending_head_roles["direct_object"]
                    and subject_index is not None
                    and any(
                        span["start"] <= subject_index <= span["end"]
                        for span in adnominal_spans
                    )
                ):
                    # Q/중심어로 관형 중심어가 본절 목적어임이 확인되면,
                    # 관형절 내부의 이/가를 본절 주어로 다시 사용하지 않는다.
                    explicit_subject_name = None
                    subject_index = None
                pending_subject_names = [
                    name
                    for _, name, _ in pending_head_roles["subject"]
                ]
                pending_subject_applies = (
                    explicit_subject_name is None
                    or explicit_subject_name in pending_subject_names
                )
                if (
                    explicit_subject_name is None
                    and pending_head_roles["subject"]
                ):
                    (
                        subject_index,
                        explicit_subject_name,
                        _,
                    ) = pending_head_roles["subject"][-1]

        topic_object_name = None
        topic_object_index = None
        topic_object_indices = set()
        if not is_relative_clause:
            if ENABLE_ROLE_NEUTRAL_AUXILIARY_PARTICLES:
                main_auxiliary_exclusions = set(role_excluded_indices)
                for span in adnominal_spans:
                    main_auxiliary_exclusions.update(
                        range(span["start"], span["end"] + 1)
                    )
                explicit_case_roles = _explicit_case_role_indices(
                    tokens,
                    role_start,
                    index,
                    excluded_indices=main_auxiliary_exclusions,
                )
                auxiliary_roles = _collect_auxiliary_particle_roles(
                    tokens,
                    role_start,
                    index,
                    excluded_indices=main_auxiliary_exclusions,
                    default_subject=default_subject_obj,
                    focus_entity=focus_entity,
                    default_object=default_object,
                    default_object_predicates=default_object_predicates,
                    base_verb=base_verb,
                    question_required_roles=question_required_roles,
                    relation_history=(
                        list(relation_history or []) + relations
                    ),
                )

                # 이/가가 있으면 은/는보다 우선한다. 이/가가 없을 때만
                # 위 근거로 확인된 보조사 주어를 사용한다.
                if explicit_case_roles["subject"]:
                    subject_index, explicit_subject_name = (
                        explicit_case_roles["subject"][-1]
                    )
                elif auxiliary_roles["subject"]:
                    (
                        subject_index,
                        explicit_subject_name,
                        _,
                    ) = auxiliary_roles["subject"][-1]
                elif subject_index is not None:
                    subject_span = _noun_span_indices(tokens, subject_index)
                    particle_index = max(subject_span) + 1
                    if (
                        particle_index < len(tokens)
                        and tokens[particle_index][1] == "JX"
                        and tokens[particle_index][0]
                        in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
                        and auxiliary_roles["context"]
                        and subject_index
                        in {
                            entry[0]
                            for entry in auxiliary_roles["context"]
                        }
                    ):
                        explicit_subject_name = None
                        subject_index = None

                if auxiliary_roles["direct_object"]:
                    (
                        topic_object_index,
                        topic_object_name,
                        topic_object_indices,
                    ) = auxiliary_roles["direct_object"][-1]
                    if (
                        explicit_subject_name == topic_object_name
                        and subject_index == topic_object_index
                    ):
                        explicit_subject_name = None
                        subject_index = None
                    elif (
                        subject_index is not None
                        and any(
                            span["start"] <= subject_index <= span["end"]
                            for span in adnominal_spans
                        )
                    ):
                        # `글씨가 흐린 기록은 버리지 않았다`에서 글씨는
                        # 관형절 주어일 뿐, 버리다의 본절 주어가 아니다.
                        explicit_subject_name = None
                        subject_index = None
                    question_topic_object_reclassified = True
                else:
                    # 새 근거가 없는 문장은 이번 변경의 대상이 아니다.
                    # 기존 약한 후보를 그대로 두어 무관한 출력 회귀를 막는다.
                    (
                        topic_object_name,
                        topic_object_index,
                        topic_object_indices,
                    ) = _find_unresolved_topic_object(
                        tokens,
                        sentence_start,
                        index,
                        base_verb,
                        excluded_indices=role_excluded_indices,
                    )
            else:
                (
                    topic_object_name,
                    topic_object_index,
                    topic_object_indices,
                ) = _find_unresolved_topic_object(
                    tokens,
                    sentence_start,
                    index,
                    base_verb,
                    excluded_indices=role_excluded_indices,
                )
            # `송편은 싸다`처럼 사물 주제 뒤에 별도 목적어가 없으면
            # 은/는 명사를 주어로 확정하지 않고 목적어 후보로 보류한다.
            if (
                topic_object_index is not None
                and subject_index == topic_object_index
                and explicit_subject_name == topic_object_name
            ):
                explicit_subject_name = None
                subject_index = None

        # 무조사 대명사 또는 자동사 바로 앞 명사는 절의 주어일 가능성이 높다.
        if (
            explicit_subject_name is None
            and index > role_start
            and index - 1 not in role_excluded_indices
            and _is_noun_token(tokens[index - 1][1])
        ):
            previous_name = _noun_name_at(tokens, index - 1)
            previous_pos = tokens[index - 1][1]
            if (
                previous_name not in TEMPORAL_NOUNS
                and not (
                    is_relative_clause
                    and previous_pos != "NP"
                    and relative_head_name not in RELATIVE_CONTENT_NOUNS
                    and relative_head_name not in TEMPORAL_NOUNS
                )
                and not (
                    base_verb == "고장나다"
                    and previous_name == "고장"
                )
                and (
                    previous_pos == 'NP'
                    or base_verb in BARE_SUBJECT_VERBS
                    or (
                        _verb_has_following_etd(tokens, index)
                        and not _verb_accepts_inferred_direct_object(base_verb)
                    )
                )
            ):
                explicit_subject_name = previous_name
                subject_index = index - 1

        # 관형절의 생략 주어가 중심 명사로 복원되는 경우.
        # '앉은 아주머니', '떡 파시던 아주머니'는 아주머니가 주어이고,
        # '고장 난 대문'처럼 자동사 관형절은 대문이 주어다.
        if (
            explicit_subject_name is None
            and is_relative_clause
            and (
                not relative_outer_subject_excluded
                or relative_head_is_object_complement
                or base_verb in BARE_SUBJECT_VERBS
                or not _verb_accepts_inferred_direct_object(base_verb)
            )
            and relative_head_name not in RELATIVE_CONTENT_NOUNS
            and relative_head_name not in TEMPORAL_NOUNS
            and (
                _is_likely_person(relative_head_name)
                or relative_head_is_object_complement
                or relative_has_object_candidate
                or base_verb in BARE_SUBJECT_VERBS
                or base_verb == "고장나다"
            )
        ):
            explicit_subject_name = relative_head_name
            subject_index = relative_head_index
            relative_subject_from_head = True

        # '생각+나다'에서 생각은 목적어나 문맥 명사가 아니라 동사의 일부다.
        compound_prefix_index = None
        if (
            base_verb in {"생각나다", "기억나다", "고장나다"}
            and index > role_start
        ):
            if tokens[index - 1][0] in {"생각", "기억", "고장"}:
                compound_prefix_index = index - 1

        if (
            relative_outer_subject_excluded
            and explicit_subject_name is None
        ):
            # 새 관형절 경계 밖에 남긴 본절 주어를 관형절 주어로 다시
            # 끌어오지 않는다. 지역 주어가 없으면 관계 생성을 보류한다.
            subject = default_subject_obj
            relative_subject_unresolved = True
        elif (
            explicit_subject_name is None
            and is_reason_clause
            and default_indirect_object is not None
            and default_indirect_object.noun != default_subject_obj.noun
        ):
            # Q의 '어머니께'는 본절의 수신자이지만, '가르쳐줘서 고맙다'의
            # 이유절에서는 생략된 행위자다. 절 안에서만 주어로 승격한다.
            subject = _get_relation_noun(
                noun_dict,
                default_indirect_object.noun,
            )
        elif explicit_subject_name is not None:
            subject = _get_relation_noun(noun_dict, explicit_subject_name)
            if not in_adnominal_scope:
                discourse_subject = subject
                sentence_subject = subject
        elif (
            inherited_clause_state is not None
            and not in_adnominal_scope
            and not is_reason_clause
            and inherited_clause_state["subjects"]
            and _connector_allows_subject_inheritance(
                inherited_clause_state["connector"],
                inherited_clause_state["subjects"][0],
            )
        ):
            subject = _get_relation_noun(
                noun_dict,
                inherited_clause_state["subjects"][0],
            )
            subject_inherited_from_connector = True
        elif (
            is_relative_clause
            and not base_verb.endswith("고 싶다")
            and (
                (
                    relative_head_name in RELATIVE_CONTENT_NOUNS
                    and not relative_has_explicit_object
                    and not (
                        relative_head_name in NOMINALIZER_NOUNS
                        and relative_bare_object_name is not None
                    )
                )
                or relative_head_name in TEMPORAL_NOUNS
                or (
                    not _verb_accepts_inferred_direct_object(base_verb)
                    and not relative_has_explicit_object
                )
            )
        ):
            # '반죽 빚는 법'처럼 관형절 주어의 근거가 없으면 화자로
            # 채우지 않고 해당 내포 관계 생성을 보류한다.
            subject = default_subject_obj
            relative_subject_unresolved = True
        elif base_verb in DEFAULT_EXPERIENCER_VERBS and focus_entity is None:
            subject = default_subject_obj
        elif is_relative_clause:
            subject = fallback_subject_obj
        else:
            subject = discourse_subject or default_subject_obj

        object_name, object_indices = _find_relation_object(
            tokens,
            role_start,
            index,
            base_verb,
            excluded_indices=(
                role_excluded_indices
                | ({subject_index} if subject_index is not None else set())
            ),
        )
        if any(
            _belongs_to_other_adnominal_clause(tokens, object_index, index)
            for object_index in object_indices
        ):
            object_name = None
            object_indices = set()

        # 준비/설명처럼 XSV 앞의 명사는 동사의 어간이지 목적어가 아니다.
        # 역할 탐색에서 이미 제외하지만, 대표 목적어 보정 이후에도 어간이
        # 남는 경우를 막는다.
        if (
            pos == 'XSV'
            and index > role_start
            and _is_noun_token(tokens[index - 1][1])
            and object_name == tokens[index - 1][0]
        ):
            object_indices.add(index - 1)
            object_name = None

        # 답변 안에서 확인된 미사용 사물 주제는 질문의 일반 목적어보다
        # 강한 근거다. 연결된 후속 동사에도 같은 주제를 유지한다.
        if object_name is None and topic_object_name is not None:
            object_name = topic_object_name
            object_indices.update(topic_object_indices)

        # 관형절 중심어가 본절의 직접목적어로 분류됐지만 일반 탐색
        # 범위 밖에 있으면 대표 목적어로 복원한다.
        if (
            object_name is None
            and pending_head_roles["direct_object"]
        ):
            _, object_name, pending_object_indices = (
                pending_head_roles["direct_object"][-1]
            )
            object_indices.update(pending_object_indices)

        if (
            object_name is None
            and relative_subject_from_head
            and relative_bare_object_name is not None
        ):
            object_name = relative_bare_object_name
            object_indices = {relative_bare_object_index}

        # '할 일', '하고 싶은 일'처럼 관형절의 중심 명사가 목적어 자리를 채운다.
        if (
            (object_name is None or object_name == subject.noun)
            and relative_head_name is not None
            and (
                relative_head_name not in RELATIVE_CONTENT_NOUNS
                or (
                    relative_head_name == "일"
                    and base_verb.endswith("고 싶다")
                )
            )
            and relative_head_name not in TEMPORAL_NOUNS
            and (
                not _is_likely_person(relative_head_name)
                or not relative_subject_from_head
            )
            and _verb_accepts_inferred_direct_object(base_verb)
            and not (
                base_verb == "돕다"
                and any(
                    token_word == "서로"
                    for token_word, _ in tokens[role_start:index]
                )
            )
        ):
            object_name = relative_head_name
            object_indices = {relative_head_index}

        # 연결된 앞 절에서 문장 안에 명시됐던 직접목적어만 이어받는다.
        # 새 주어가 등장했거나 현재 동사가 직접목적어를 받지 않으면 넘기지 않는다.
        if (
            object_name is None
            and inherited_clause_state is not None
            and inherited_clause_state["direct_objects"]
            and not is_relative_clause
            and not is_reason_clause
            and _connector_allows_object_inheritance(
                inherited_clause_state["connector"]
            )
            and _verb_accepts_inferred_direct_object(base_verb)
            and base_verb not in OBJECT_INHERITANCE_BLOCKED_VERBS
            and _shared_direct_object_is_compatible(
                base_verb,
                inherited_clause_state["direct_objects"][0],
            )
            and (
                explicit_subject_name is None
                or explicit_subject_name in inherited_clause_state["subjects"]
                or inherited_clause_state["connector"]
                in (
                    REASON_CONNECTIVE_ENDINGS
                    | IMMEDIATE_ACTION_CONNECTIVES
                    | TEMPORAL_SEQUENCE_CONNECTIVES
                )
            )
        ):
            object_name = inherited_clause_state["direct_objects"][0]
            object_indices = set()
            object_inherited_from_connector = True

        # 질문 중심어는 관형절의 주어로 사용하지 않는다. 관형절 내부에
        # 목적어가 없고, 이전 대화에서 같은 동사-중심어가 직접목적어
        # 관계로 확인된 경우에만 가장 낮은 우선순위의 목적어로 보완한다.
        history_confirms_relative_object = (
            is_relative_clause
            and relative_subject_unresolved
            and object_name is None
            and not relative_has_explicit_object
            and default_object is not None
            and default_object.noun != subject.noun
            and default_object.noun != relative_head_name
            and _history_confirms_direct_object(
                relation_history,
                base_verb,
                default_object.noun,
            )
        )
        if history_confirms_relative_object:
            object_name = default_object.noun
            object_indices = set()
            # 확인된 목적어가 있으므로 기본 화자를 관형절 주어로 유지한 채
            # 관계 생성 보류만 해제한다.
            relative_subject_unresolved = False

        if (
            object_name is None
            and default_object is not None
            and default_object.noun != subject.noun
            and (
                _verb_accepts_inferred_direct_object(base_verb)
                or (
                    default_object_predicates is not None
                    and base_verb in default_object_predicates
                )
            )
            and not relative_subject_unresolved
        ):
            object_name = default_object.noun
            object_indices = set()

        if compound_prefix_index is not None:
            object_indices.add(compound_prefix_index)
            if object_name in {"생각", "기억"}:
                object_name = None

        # 기억된 중심 인물이 문장 안에서 직접목적어로 명시되면 화자가 주어다.
        if (
            explicit_subject_name is None
            and focus_entity is not None
            and object_name == subject.noun
        ):
            subject = default_subject_obj
            if not in_adnominal_scope:
                discourse_subject = default_subject_obj
                sentence_subject = default_subject_obj

        if (
            question_topic_object_reclassified
            and object_name == subject.noun
        ):
            subject = default_subject_obj
            if not in_adnominal_scope:
                discourse_subject = default_subject_obj
                sentence_subject = default_subject_obj

        # 명사절을 보는 주체는 내포절 주어가 아니라 기본 화자다.
        if (
            base_verb == "보다"
            and explicit_subject_name is None
            and object_name in NOMINALIZER_NOUNS
        ):
            subject = default_subject_obj

        subject_names, subject_role_indices = (
            _collect_coordinated_subject_roles(
                tokens,
                role_start,
                subject.noun,
                subject_index,
                excluded_indices=role_excluded_indices,
            )
        )
        if subject_inherited_from_connector:
            subject_names = list(inherited_clause_state["subjects"]) + [
                name
                for name in subject_names
                if name not in inherited_clause_state["subjects"]
            ]
        if pending_subject_applies:
            pending_subject_names = [
                name
                for _, name, _ in pending_head_roles["subject"]
            ]
            subject_names = pending_subject_names + [
                name for name in subject_names
                if name not in pending_subject_names
            ]
            for _, _, role_indices in pending_head_roles["subject"]:
                subject_role_indices.update(role_indices)

        direct_names, direct_role_indices = _collect_direct_object_roles(
            tokens,
            role_start,
            index,
            base_verb,
            object_name,
            object_indices,
            excluded_indices=(
                role_excluded_indices
                | subject_role_indices
            ),
            adnominal_spans=adnominal_spans,
        )
        pending_direct_names = [
            name
            for _, name, _ in pending_head_roles["direct_object"]
        ]
        direct_names = pending_direct_names + [
            name for name in direct_names
            if name not in pending_direct_names
        ]
        if object_inherited_from_connector:
            direct_names = list(inherited_clause_state["direct_objects"]) + [
                name
                for name in direct_names
                if name not in inherited_clause_state["direct_objects"]
            ]
        for _, _, role_indices in pending_head_roles["direct_object"]:
            direct_role_indices.update(role_indices)

        indirect_names, indirect_role_indices = _collect_indirect_object_roles(
            tokens,
            role_start,
            index,
            base_verb,
            subject,
            default_subject_obj,
            default_indirect_object=default_indirect_object,
            excluded_indices=role_excluded_indices,
            allow_default_inference=not is_relative_clause,
        )
        if (
            not indirect_names
            and pending_reason_clauses
            and default_indirect_object is not None
            and default_indirect_object.noun != subject.noun
            and base_verb in REASON_TARGET_PREDICATES
        ):
            # 이유절의 행위자는 '어머니가 가르쳐줘서'에서 주어이지만,
            # '어머니께 말하고 싶다' 본절에서는 다시 수신자가 된다.
            indirect_names.append(default_indirect_object.noun)
        pending_indirect_names = [
            name
            for _, name, _ in pending_head_roles["indirect_object"]
        ]
        indirect_names = pending_indirect_names + [
            name for name in indirect_names
            if name not in pending_indirect_names
        ]
        for _, _, role_indices in pending_head_roles["indirect_object"]:
            indirect_role_indices.update(role_indices)

        if (
            not indirect_names
            and remembered_focus_entity is not None
            and remembered_focus_entity.noun != default_subject_obj.noun
            and _is_beneficiary_verb(base_verb)
            and _verb_is_followed_by_conditional(tokens, index)
        ):
            indirect_names.append(remembered_focus_entity.noun)
        complement_names, complement_role_indices = (
            _collect_object_complement_roles(
                tokens,
                role_start,
                index,
                base_verb,
                excluded_indices=role_excluded_indices,
            )
        )
        pending_complement_names = [
            name
            for _, name, _ in pending_head_roles["object_complement"]
        ]
        complement_names = pending_complement_names + [
            name for name in complement_names
            if name not in pending_complement_names
        ]
        for _, _, role_indices in pending_head_roles["object_complement"]:
            complement_role_indices.update(role_indices)

        used_indices = set(object_indices)
        used_indices.update(direct_role_indices)
        used_indices.update(indirect_role_indices)
        used_indices.update(complement_role_indices)
        used_indices.update(role_excluded_indices)
        used_indices.update(subject_role_indices)
        for role_entries in pending_head_roles.values():
            for _, _, role_indices in role_entries:
                used_indices.update(role_indices)
        if compound_prefix_index is not None:
            used_indices.add(compound_prefix_index)

        context = _collect_relation_context(
            tokens, role_start, index, used_indices
        )
        pending_context_names = [
            name
            for _, name, _ in pending_head_roles["context"]
        ]
        context = pending_context_names + [
            name for name in context
            if name not in pending_context_names
        ]
        polarity_bit, negation = detect_polarity_bit(
            tokens,
            index,
            clause_start=role_start,
        )

        unresolved_relative_subject = relative_subject_unresolved
        leads_to_relative_predicate = False
        if (
            not is_relative_clause
            and object_name is None
            and not direct_names
            and index + 1 < len(tokens)
            and tokens[index + 1][1].startswith("EC")
        ):
            for later_index in range(index + 2, len(tokens)):
                _, later_pos = tokens[later_index]
                if later_pos == "SF" or _is_noun_token(later_pos):
                    break
                if later_pos in {"VV", "VXV", "XSV"}:
                    leads_to_relative_predicate = _verb_has_following_etd(
                        tokens,
                        later_index,
                    )
                    if (
                        leads_to_relative_predicate
                        and ENABLE_ADNOMINAL_CONNECTOR_BOUNDARY
                        and _shareable_clause_connector_after(tokens, index)
                        in ADNOMINAL_CLAUSE_BOUNDARY_CONNECTIVES
                    ):
                        # `짖어서 [잠자던 강아지]`, `멎은 뒤 [젖은
                        # 천막]`은 앞 술어와 관형 술어가 별도 절이다.
                        # 앞 술어를 분석기 분절 조각으로 보고 버리지 않는다.
                        leads_to_relative_predicate = False
                    break

        # 기존 의도대로 존재 동사는 관계 목록에서 제외한다.
        temporal_relative = relative_head_name in TEMPORAL_NOUNS
        idiomatic_people_living = (
            base_verb == "살다"
            and explicit_subject_name == "사람"
            and relative_head_name == "정"
        )
        if (
            base_verb != "있다"
            and not unresolved_relative_subject
            and not leads_to_relative_predicate
            and not temporal_relative
            and not idiomatic_people_living
        ):
            obj = (
                _get_relation_noun(noun_dict, object_name)
                if object_name is not None
                else None
            )
            if obj is None or obj.noun != subject.noun:
                direct_objects = [
                    _get_relation_noun(noun_dict, noun_name)
                    for noun_name in direct_names
                    if noun_name != subject.noun
                ]
                indirect_objects = [
                    _get_relation_noun(noun_dict, noun_name)
                    for noun_name in indirect_names
                    if noun_name != subject.noun
                ]
                object_complements = [
                    _get_relation_noun(noun_dict, noun_name)
                    for noun_name in complement_names
                ]
                subjects = [
                    _get_relation_noun(noun_dict, noun_name)
                    for noun_name in subject_names
                ]
                if is_relative_clause:
                    if subject.noun == relative_head_name:
                        head_role = "subject"
                    elif (
                        object_name == relative_head_name
                        or relative_head_name in direct_names
                    ):
                        head_role = "direct_object"
                    else:
                        head_role = "content"

                    (
                        attachment_names,
                        coordinated_role_names,
                    ) = _relative_attachment_names(
                        tokens,
                        relative_head_name,
                        relative_head_index,
                    )
                    relative_subject_names = [
                        item.noun for item in subjects
                    ]
                    relative_direct_names = [
                        item.noun for item in direct_objects
                    ]
                    if head_role == "subject":
                        relative_subject_names.extend(
                            name for name in coordinated_role_names
                            if name not in relative_subject_names
                        )
                    elif head_role == "direct_object":
                        relative_direct_names.extend(
                            name for name in coordinated_role_names
                            if name not in relative_direct_names
                        )

                    relative_clause = RelativeClause(
                        subject=subject.noun,
                        verb=base_verb,
                        obj=(obj.noun if obj is not None else None),
                        head_role=head_role,
                        polarity_bit=polarity_bit,
                        negation=negation,
                        context_nouns=context,
                        direct_objects=relative_direct_names,
                        indirect_objects=[
                            item.noun for item in indirect_objects
                        ],
                        object_complements=[
                            item.noun for item in object_complements
                        ],
                        subjects=relative_subject_names,
                    )
                    for attachment_name in attachment_names:
                        _get_relation_noun(
                            noun_dict,
                            attachment_name,
                        ).add_relative_clause(relative_clause)
                else:
                    formatted_context = []
                    for context_name in context:
                        context_noun = _get_relation_noun(
                            noun_dict,
                            context_name,
                        )
                        formatted_context.append(
                            context_noun.format_for_relation()
                            if context_noun.relative_clauses
                            else context_name
                        )
                    relation = Relation(
                            subject,
                            base_verb,
                            obj,
                            indirect_object=(
                                indirect_objects[0]
                                if indirect_objects
                                else None
                            ),
                            polarity_bit=polarity_bit,
                            negation=negation,
                            context_nouns=formatted_context,
                            direct_objects=direct_objects,
                            indirect_objects=indirect_objects,
                            object_complements=object_complements,
                            subjects=subjects,
                            reason_clauses=(
                                [] if is_reason_clause
                                else pending_reason_clauses
                            ),
                        )
                    if is_reason_clause:
                        pending_reason_clauses.append(relation)
                    else:
                        relations.append(relation)
                        pending_reason_clauses = []

        connector = _shareable_clause_connector_after(tokens, index)
        if (
            ENABLE_CONNECTED_CLAUSE_SHARING
            and connector is not None
            and not in_adnominal_scope
            and not is_reason_clause
        ):
            connected_subjects = [subject.noun] + [
                name for name in subject_names if name != subject.noun
            ]
            direct_object_has_sentence_evidence = (
                object_inherited_from_connector
                or _has_explicit_direct_object(tokens, role_start, index)
            )
            connected_clause_state = {
                "subjects": (
                    []
                    if connector in TEMPORAL_SEQUENCE_CONNECTIVES
                    else connected_subjects
                ),
                "direct_objects": (
                    list(direct_names)
                    if direct_object_has_sentence_evidence
                    else []
                ),
                "connector": connector,
            }
            if connector in TEMPORAL_SEQUENCE_CONNECTIVES:
                # 시간절 안의 명시 주어는 뒤 본절 및 다음 문장에 남기지
                # 않는다. 목적어 상태와 분리해 Q의 중심 주어/화자로 복귀한다.
                discourse_subject = answer_subject_obj
                sentence_subject = answer_subject_obj
        elif in_adnominal_scope and inherited_clause_state is not None:
            # `우리는 걷고 [얼어붙은 다리]를 건넜다`처럼 연결된 두
            # 본절 사이에 관형절이 끼어도 앞 본절의 주어 공유 상태는
            # 다음 본절까지 유지한다. 관형절 자체에는 위의 주어 선택
            # 조건에서 이 상태를 사용하지 않는다.
            connected_clause_state = inherited_clause_state
        else:
            connected_clause_state = None

        if in_adnominal_scope or is_reason_clause:
            # 관형절의 주어는 뒤의 주절까지 이어받지 않는다.
            discourse_subject = answer_subject_obj
        elif (
            explicit_subject_name is not None
            and not _is_likely_person(subject.noun)
        ):
            discourse_subject = answer_subject_obj

        # '깎아주면 잘 먹어'처럼 조건절의 수혜자가 다음 절의 생략 주어가 된다.
        if (
            not in_adnominal_scope
            and
            indirect_names
            and _is_beneficiary_verb(base_verb)
            and _verb_is_followed_by_conditional(tokens, index)
        ):
            discourse_subject = _get_relation_noun(noun_dict, indirect_names[0])
            sentence_subject = discourse_subject

        if is_relative_clause:
            pending_relative_indices.update(relative_internal_indices)
            pending_relative_head_indices.add(relative_head_index)
        elif is_adnominal_internal_predicate:
            # 같은 관형절 안에서 뒤에 올 실제 관형 술어가 앞쪽 주어와
            # 논항을 다시 읽을 수 있도록 본절용 범위 상태를 건드리지 않는다.
            pass
        else:
            pending_relative_indices = set()
            pending_relative_head_indices = set()
            predicate_start = index + 1
        index += 1

    if pending_reason_clauses:
        relations.extend(pending_reason_clauses)
    return relations


def map_josa_to_kkma_tag(josa: str) -> str:
    josa_tag_map = {
        "이": "JKS", "가": "JKS",
        "은": "JX", "는": "JX",
        "을": "JKO", "를": "JKO",
        "에": "JKM", "에서": "JKM", "에게": "JKM", "에게서": "JKM", "한테": "JKM", "한테서": "JKM",
        "께": "JKM", "로": "JKM", "으로": "JKM",
        "의": "JKG",
        "와": "JC", "과": "JC", "랑": "JC", "이랑": "JC",
        "도": "JX", "만": "JX", "부터": "JX", "까지": "JX", "마다": "JX", "쯤": "JX",
    }
    return josa_tag_map.get(josa, "JX")


def _okt_bare_noun_adnominal_candidates(sentence: str):
    """Okt가 확인한 `무조사 명사 + 관형 술어 + 명사` 후보를 반환한다.

    대부분의 무조사 명사는 KKMA도 NNG로 분석하므로 기존 관형절 범위
    규칙이 그대로 처리한다. 이 후보는 `먼지`를 멀/VA+ㄴ지/ECD처럼
    잘못 나눈 경우만 관계 생성 전에 복원하기 위한 교차 분석 근거다.
    """
    if not ENABLE_BARE_NOUN_ADNOMINAL_REPAIR:
        return []

    surface_words = re.findall(r"[가-힣]+", sentence)
    candidates = []
    for index in range(len(surface_words) - 2):
        noun_surface = surface_words[index]
        predicate_surface = surface_words[index + 1]
        head_surface = surface_words[index + 2]
        noun_tokens = okt.pos(noun_surface, norm=True, stem=True)
        predicate_tokens = okt.pos(
            predicate_surface,
            norm=True,
            stem=True,
        )
        head_tokens = okt.pos(head_surface, norm=True, stem=False)
        if (
            len(noun_tokens) != 1
            or noun_tokens[0][1] != "Noun"
            or len(predicate_tokens) != 1
            or predicate_tokens[0][1] not in {"Verb", "Adjective"}
            or not any(pos == "Noun" for _, pos in head_tokens)
        ):
            continue
        predicate_base = predicate_tokens[0][0]
        predicate_stem = (
            predicate_base[:-1]
            if predicate_base.endswith("다")
            else predicate_base
        )
        candidates.append(
            {
                "noun": noun_surface,
                "predicate_stem": predicate_stem,
                "used": False,
            }
        )
    return candidates


def _repair_confirmed_surface_verb_forms(sentence, raw_tokens):
    """원문 활용형이 입증하는 경우에만 KKMA의 동사 어간을 바로잡는다.

    `달았어`와 `닫았어`는 표면 철자가 다르지만 KKMA는 둘 다
    `닫/VV+았/EPT`로 분석한다. 원문의 등장 순서와 같은 분석열의 등장
    순서가 일치할 때 `달았-`에 대응하는 어간만 `달/VV`로 바꾼다.
    따라서 실제 `닫다` 원형이나 `닫았-` 활용은 수정하지 않는다.
    """
    repaired = list(raw_tokens)
    rules = (
        (
            re.compile(r"(?<![가-힣])(?P<surface>달았|닫았)"),
            ("닫", "VV"),
            ("았", "EPT"),
            "달았",
            ("달", "VV"),
        ),
        (
            re.compile(r"(?<![가-힣])(?P<surface>달아|닫아)"),
            ("닫", "VV"),
            ("아", None),
            "달아",
            ("달", "VV"),
        ),
    )

    for pattern, wrong_stem, following, target_surface, corrected_stem in rules:
        surfaces = [
            match.group("surface")
            for match in pattern.finditer(sentence)
        ]
        following_word, following_pos = following
        analyzed_indices = [
            index
            for index in range(len(raw_tokens) - 1)
            if (
                raw_tokens[index] == wrong_stem
                and raw_tokens[index + 1][0] == following_word
                and (
                    following_pos is None
                    or raw_tokens[index + 1][1] == following_pos
                )
            )
        ]
        # 정렬에 확신이 없으면 보정하지 않는다. 잘못된 위치를 고치는
        # 것보다 확인된 활용형 하나를 놓치는 편이 안전하다.
        if len(surfaces) != len(analyzed_indices):
            continue
        for surface, token_index in zip(surfaces, analyzed_indices):
            if surface == target_surface:
                repaired[token_index] = corrected_stem

    return repaired


def postprocess_kkma_pos(sentence: str):
    raw = kkma.pos(sentence)
    original_raw = list(raw)
    raw = _repair_confirmed_surface_verb_forms(sentence, raw)
    bare_noun_adnominal_candidates = (
        _okt_bare_noun_adnominal_candidates(sentence)
    )

    # KKMA가 자주 틀리는 활용/복수 접미 패턴을 관계 분석 전에 보정한다.
    corrected_raw = []
    index = 0
    while index < len(raw):
        word, pos = raw[index]

        # `먼지 묻은`을 KKMA가 멀/VA+ㄴ지/ECD+묻/VV+은/ETD로
        # 분석하는 유형이다. 원문의 앞 어절을 Okt가 단일 명사로,
        # 다음 어절을 단일 관형 술어로 확인한 경우에만 앞 두 토큰을
        # 명사 하나로 되돌린다. `멀지 않은 곳`처럼 Okt도 형용사로
        # 판단하는 표현과 XSV 명사 어간은 이 조건에 들어오지 않는다.
        if (
            # 실제 용언 연결 `보이지 않는`은 보이/VV+지/ECD이므로
            # 복원하지 않는다. 현재 보정은 명사를 형용사로 오인한
            # VA+EC 패턴으로 한정한다.
            pos == "VA"
            and index + 3 < len(raw)
            and raw[index + 1][1].startswith("EC")
            and raw[index + 2][1] in {"VV", "VXV", "VA"}
            and raw[index + 3][1] == "ETD"
        ):
            matching_candidate = next(
                (
                    candidate
                    for candidate in bare_noun_adnominal_candidates
                    if (
                        not candidate["used"]
                        and candidate["predicate_stem"]
                        == raw[index + 2][0]
                    )
                ),
                None,
            )
            if matching_candidate is not None:
                corrected_raw.append((matching_candidate["noun"], "NNG"))
                matching_candidate["used"] = True
                index += 2
                continue

        # KKMA가 하나의 `명사+조사` 어절을 `조/NNG+수가/NNG`,
        # `수의/NNG+사가/NNG`처럼 명사 둘로 잘못 자르는 경우가 있다.
        # 관계를 만든 뒤 Imagine 객체만 합치면 조사 역할은 복구되지 않으므로,
        # 원문에서 한 어절이고 Okt도 명사구+조사로 확인한 경우에만 먼저
        # `조수/NNG+가/JKS` 형태로 복원한다. 확인되지 않은 NNG+NNG는
        # `나무 칼`, `별 지도` 같은 기존 합성명사 처리에 그대로 맡긴다.
        repaired_noun_particle = False
        if (
            ENABLE_PRE_RELATION_NOUN_PARTICLE_REPAIR
            and _is_noun_token(pos)
            and index + 1 < len(raw)
            and _is_noun_token(raw[index + 1][1])
        ):
            next_word, _ = raw[index + 1]
            for particle in ("은", "는", "이", "가", "을", "를", "도"):
                if len(next_word) <= len(particle) or not next_word.endswith(particle):
                    continue
                reconstructed = word + next_word[:-len(particle)]
                surface = reconstructed + particle
                if not re.search(
                    rf"(?<![가-힣]){re.escape(surface)}(?![가-힣])",
                    sentence,
                ):
                    continue
                okt_tokens = okt.pos(surface, norm=True, stem=True)
                if (
                    len(okt_tokens) >= 2
                    and okt_tokens[-1] == (particle, "Josa")
                    and "".join(token for token, _ in okt_tokens[:-1])
                    == reconstructed
                    and all(
                        token_pos in {"Noun", "Modifier"}
                        for _, token_pos in okt_tokens[:-1]
                    )
                ):
                    noun_pos = "NNP" if pos == "NNP" else "NNG"
                    corrected_raw.extend(
                        [(reconstructed, noun_pos), (particle, map_josa_to_kkma_tag(particle))]
                    )
                    index += 2
                    repaired_noun_particle = True
                    break
        if repaired_noun_particle:
            continue

        # KKMA가 `젖은 벌`, `남은 빵`의 관형형을
        # 젖/NNG+은/JX, 남/NNG+은/JX처럼 명사 주제로 오인하는 경우.
        # 단어 목록을 두지 않고 `명사+은/는+명사`가 원문에서 이어진
        # 어절일 때 앞 어절 전체를 Okt로 재분석한다. Okt도 명사+조사로
        # 판단하는 `봄은 꽃`, `손은 문`은 그대로 유지한다.
        if (
            _is_noun_token(pos)
            and index + 2 < len(raw)
            and raw[index + 1][0] in SUBJECT_TOPIC_PARTICLES
            and raw[index + 1][1] == "JX"
            and _is_noun_token(raw[index + 2][1])
        ):
            particle = raw[index + 1][0]
            following_noun = raw[index + 2][0]
            surface = word + particle
            if re.search(
                rf"(?<![가-힣]){re.escape(surface)}"
                rf"(?=\s+{re.escape(following_noun)})",
                sentence,
            ):
                okt_tokens = okt.pos(surface, norm=True, stem=True)
                if (
                    len(okt_tokens) == 1
                    and okt_tokens[0][1] in {"Verb", "Adjective"}
                ):
                    base = okt_tokens[0][0]
                    stem = base[:-1] if base.endswith("다") else base
                    predicate_pos = (
                        "VV" if okt_tokens[0][1] == "Verb" else "VA"
                    )
                    corrected_raw.extend(
                        [(stem, predicate_pos), (particle, "ETD")]
                    )
                    index += 2
                    continue

        # 동사 관형형 뒤의 의존명사 `뒤/다음/후/전`이 새로운 술어로
        # 이어지면 관형절의 중심 명사가 아니라 시간적 절 경계다.
        # `집 뒤`, `다음 사람`, `전을 먹다` 같은 일반 명사 용법은
        # 앞 토큰이 동사의 ETD/ETN인 경우가 아니므로 그대로 둔다.
        if (
            pos in {"ETD", "ETN"}
            and index + 1 < len(raw)
            and raw[index + 1][0] in TEMPORAL_SEQUENCE_CONNECTIVES
            and _is_noun_token(raw[index + 1][1])
        ):
            temporal_word = raw[index + 1][0]
            previous_predicate = next(
                (
                    previous
                    for previous in range(index - 1, -1, -1)
                    if raw[previous][1]
                    in {"VV", "VXV", "XSV", "VA", "XSA", "SF"}
                    or _is_noun_token(raw[previous][1])
                ),
                None,
            )
            has_action_predicate = (
                previous_predicate is not None
                and raw[previous_predicate][1] in {"VV", "VXV", "XSV"}
            )
            has_later_predicate = any(
                later_pos in {"VV", "VXV", "XSV", "VA", "XSA"}
                for _, later_pos in raw[index + 2:]
            )
            next_phrase_end = index + 2
            while (
                next_phrase_end < len(raw)
                and _is_noun_token(raw[next_phrase_end][1])
            ):
                next_phrase_end += 1
            following_particle = (
                raw[next_phrase_end]
                if next_phrase_end < len(raw)
                else (None, None)
            )
            has_previous_object = False
            for previous in range(index - 1, -1, -1):
                previous_pos = raw[previous][1]
                if previous_pos == "SF" or previous_pos.startswith("EC"):
                    break
                if previous_pos == "JKO":
                    has_previous_object = True
                    break
            pancake_object = (
                temporal_word == "전"
                and pos == "ETD"
                and following_particle[1] in {"JKO", "JKS", "JX", "JC"}
            )
            next_noun_modifier = (
                temporal_word == "다음"
                and index + 2 < len(raw)
                and _is_noun_token(raw[index + 2][1])
                and following_particle[1] in {"JKO", "JKS", "JX", "JC"}
                and not has_previous_object
            )
            if (
                has_action_predicate
                and has_later_predicate
                and not pancake_object
                and not next_noun_modifier
            ):
                corrected_raw.append((f"{word} {temporal_word}", "ECE"))
                index += 2
                continue

        # `-고 나서`를 나/NP+서/JKM으로 오인하면 화자 객체가 문맥에
        # 추가된다. 원문과 앞 연결어미가 모두 확인된 경우에만 하나의
        # 담화 연결 표지로 복원한다.
        if (
            (word, pos) == ("나", "NP")
            and index > 0
            and raw[index - 1][0] == "고"
            and raw[index - 1][1].startswith("EC")
            and index + 1 < len(raw)
            and raw[index + 1] == ("서", "JKM")
            and re.search(r"고\s+나서", sentence)
        ):
            corrected_raw.append(("나서", "ECD"))
            index += 2
            continue

        # 연결절 뒤의 명시적 화자 `나는`을 KKMA가 나/VXV+는/ETD로
        # 오인하는 경우를 복원한다. 절 앞에 연결어미가 있고 뒤에
        # 명시 목적어와 본동사가 있을 때만 적용해 `나는 새`의 실제
        # 관형절 해석 가능성은 보존한다.
        if (
            word == "나"
            and pos in {"VV", "VXV"}
            and index + 1 < len(raw)
            and raw[index + 1] == ("는", "ETD")
            and re.search(r"(?<![가-힣])나는(?=\s)", sentence)
        ):
            has_previous_connector = False
            at_sentence_start = True
            for previous in range(index - 1, -1, -1):
                previous_pos = raw[previous][1]
                if previous_pos == "SF":
                    break
                if previous_pos.startswith("EC"):
                    has_previous_connector = True
                    break
                if previous_pos not in {"SP", "SS", "SW"}:
                    at_sentence_start = False
            has_following_object = False
            has_following_main_predicate = False
            for later in range(index + 2, len(raw)):
                later_pos = raw[later][1]
                if later_pos == "SF":
                    break
                if later_pos == "JKO":
                    has_following_object = True
                if later_pos in {"VV", "XSV"}:
                    has_following_main_predicate = True
                    break
            if (
                (has_previous_connector or at_sentence_start)
                and has_following_object
                and has_following_main_predicate
            ):
                corrected_raw.extend([("나", "NP"), ("는", "JX")])
                index += 2
                continue

        # '누구에게나'의 보조사 '나'를 나/VV+아/ECS로 오인하는 경우.
        if (
            (word, pos) in {("에게", "JKM"), ("한테", "JKM"), ("께", "JKM")}
            and index + 2 < len(raw)
            and raw[index + 1] == ("나", "VV")
            and raw[index + 2] == ("아", "ECS")
            and re.search(rf"{re.escape(word)}나(?=\s|$)", sentence)
        ):
            corrected_raw.extend([(word, pos), ("나", "JX")])
            index += 3
            continue

        # '꼼꼼한 사람', '바삭한 전'의 관형형 형용사를
        # 꼼꼼/MAG+한/MDN, 바삭/MAG+한/MDN으로 오인하는 경우.
        # 원문에서 어근과 '한'이 붙어 있을 때만 VA+ETD로 복원하므로
        # '아주 한 사람', '대략 한 그릇' 같은 실제 수량 표현은 유지한다.
        if (
            pos == "MAG"
            and index + 2 < len(raw)
            and raw[index + 1] == ("한", "MDN")
            and _is_noun_token(raw[index + 2][1])
            and re.search(
                rf"(?<![가-힣]){re.escape(word)}한"
                rf"(?=\s*{re.escape(raw[index + 2][0])})",
                sentence,
            )
            and not re.search(
                rf"(?<![가-힣]){re.escape(word)}\s+한"
                rf"(?=\s+{re.escape(raw[index + 2][0])})",
                sentence,
            )
        ):
            corrected_raw.extend([(word + "하", "VA"), ("ㄴ", "ETD")])
            index += 2
            continue

        # '온 가족'의 관형사를 오/VV+ㄴ/ETD로 오인하는 경우.
        if (
            (word, pos) == ("오", "VV")
            and index + 2 < len(raw)
            and raw[index + 1] == ("ㄴ", "ETD")
            and raw[index + 2][0] == "가족"
            and re.search(r"온\s+가족", sentence)
        ):
            corrected_raw.append(("온", "MDT"))
            index += 2
            continue

        # '열여덟 살 때'의 단위명사를 사/VV+ㄹ/ETD로 오인하는 경우.
        if (
            (word, pos) == ("사", "VV")
            and index > 0
            and raw[index - 1][1] == "NR"
            and index + 2 < len(raw)
            and raw[index + 1] == ("ㄹ", "ETD")
            and raw[index + 2][0] == "때"
            and re.search(r"살\s+때", sentence)
        ):
            corrected_raw.append(("살", "NNB"))
            index += 2
            continue

        # '집들이 모여'에서 집들이/NNG를 집들/NNG+이/JKS로 복원한다.
        if (
            (word, pos) == ("집들이", "NNG")
            and index + 1 < len(raw)
            and raw[index + 1][0] == "모이"
            and re.search(r"집들이\s+모여", sentence)
        ):
            corrected_raw.extend([("집들", "NNG"), ("이", "JKS")])
            index += 1
            continue

        # 상/NNG+인들/JX+의/JKG로 잘못 나뉜 '상인들의'.
        if (
            (word, pos) == ("상", "NNG")
            and index + 2 < len(raw)
            and raw[index + 1] == ("인들", "JX")
            and raw[index + 2] == ("의", "JKG")
            and "상인들의" in sentence
        ):
            corrected_raw.extend([("상인들", "NNG"), ("의", "JKG")])
            index += 3
            continue

        # 대명사 '나+를'을 관형형 동사 '나르+ㄹ'로 오인하는 경우.
        # 같은 절 앞에 이미 목적격 조사가 있으면 실제 '나를 사람'일 수 있으므로
        # 그 경우에는 원래 분석을 유지한다.
        if (
            (word, pos) == ("나르", "VV")
            and index + 1 < len(raw)
            and raw[index + 1] == ("ㄹ", "ETD")
            and re.search(r"(?<![가-힣])나를(?=\s|[,.!?])", sentence)
        ):
            has_recent_object = False
            for previous_index in range(index - 1, -1, -1):
                previous_pos = raw[previous_index][1]
                if previous_pos == "SF" or previous_pos.startswith("EC"):
                    break
                if previous_pos == "JKO":
                    has_recent_object = True
                    break
            if not has_recent_object:
                corrected_raw.extend([("나", "NP"), ("를", "JKO")])
                index += 2
                continue

        # 르 불규칙 활용을 KKMA가 '불르/VV'로 남기는 경우.
        if (word, pos) == ("불르", "VV") and "불렀" in sentence:
            corrected_raw.append(("부르", "VV"))
            index += 1
            continue

        # '노랬어'를 노래/VV+었으로 오인하는 경우.
        if (
            (word, pos) == ("노래", "VV")
            and index + 1 < len(raw)
            and raw[index + 1] == ("었", "EPT")
            and "노랬" in sentence
        ):
            corrected_raw.append(("노랗", "VA"))
            index += 1
            continue

        # '빚던'을 빚/NNG+덜/VV+ㄴ/ETD로 오인하는 경우.
        if (
            (word, pos) == ("빚", "NNG")
            and index + 2 < len(raw)
            and raw[index + 1] == ("덜", "VV")
            and raw[index + 2] == ("ㄴ", "ETD")
            and "빚던" in sentence
        ):
            corrected_raw.extend([("빚", "VV"), ("더", "EPT")])
            index += 2
            continue

        # '친해져서'를 친/NNG+해지/VV로 분리하는 경우.
        if (
            (word, pos) == ("친", "NNG")
            and index + 1 < len(raw)
            and raw[index + 1] == ("해지", "VV")
            and re.search(r"친해(?:지|져)", sentence)
        ):
            corrected_raw.append(("친해지", "VV"))
            index += 2
            continue

        # '고장 난'을 고장/NNG+낳/VV+ㄴ/ETD로 오인하는 경우.
        if (
            (word, pos) == ("고장", "NNG")
            and index + 2 < len(raw)
            and raw[index + 1] == ("낳", "VV")
            and raw[index + 2] == ("ㄴ", "ETD")
            and re.search(r"고장\s+난", sentence)
        ):
            corrected_raw.extend([("고장", "NNG"), ("나", "VV")])
            index += 2
            continue

        # KKMA가 동반/접속 조사 와·과를 목적격이나 부사격으로 태깅하는 경우.
        if word in {"와", "과", "랑", "이랑"} and pos in {"JKO", "JKM", "JX"}:
            corrected_raw.append((word, "JC"))
            index += 1
            continue

        # '신기하다며'를 신기/NNG + 하다/NNP + 며/JC로 오인하는 패턴.
        if (
            word == "신기"
            and pos.startswith("N")
            and index + 2 < len(raw)
            and raw[index + 1] == ("하다", "NNP")
            and raw[index + 2] == ("며", "JC")
        ):
            corrected_raw.extend([("신기하", "VA"), ("다며", "ECE")])
            index += 3
            continue

        # '김치전과'를 김치/NNG + 전과/NNG로 잘못 자르는 패턴.
        if (
            word == "전과"
            and pos.startswith("N")
            and corrected_raw
            and corrected_raw[-1][0] == "김치"
            and _is_noun_token(corrected_raw[-1][1])
        ):
            corrected_raw[-1] = ("김치전", corrected_raw[-1][1])
            corrected_raw.append(("과", "JC"))
            index += 1
            continue

        # 복수 접미사와 사람 명사 형성 접미사는 앞 명사와 결합한다.
        # '씩'까지 결합하면 '사진 한 장씩'의 수량 표현이 명사로 오인되므로 제외한다.
        # 예: 친척+들 -> 친척들, 순+자 -> 순자
        if (
            _is_noun_token(pos)
            and index + 1 < len(raw)
            and raw[index + 1][1] == "XSN"
            and raw[index + 1][0] in {"들", "자"}
        ):
            corrected_raw.append((word + raw[index + 1][0], pos))
            index += 2
            continue

        # '떡 파시던'을 파시/NNG + 덜/VV로 오인하는 패턴.
        if (
            word == "파시"
            and pos.startswith('N')
            and index + 2 < len(raw)
            and raw[index + 1] == ("덜", "VV")
            and raw[index + 2][1] == 'ETD'
        ):
            corrected_raw.extend([("파", "VV"), ("시", "EPH"), ("더", "EPT")])
            index += 2
            continue

        # '생선도 사고 두부도'와 '장기도 두고 이야기'의 연결형 보정.
        if (
            word in {"사고", "두고"}
            and pos.startswith('N')
            and index > 0
            and raw[index - 1][1] == 'JX'
            and index + 1 < len(raw)
            and _is_noun_token(raw[index + 1][1])
        ):
            corrected_raw.append(("사" if word == "사고" else "두", "VV"))
            corrected_raw.append(("고", "ECE"))
            index += 1
            continue

        # '갔어도'처럼 미등록어(UN)가 된 활용형은 OKT 기본형으로 복원한다.
        if pos == 'UN':
            okt_tokens = okt.pos(word, norm=True, stem=True)
            if len(okt_tokens) == 1 and okt_tokens[0][1] == "Verb":
                corrected_raw.append((okt_tokens[0][0], "VV"))
                index += 1
                continue

        corrected_raw.append((word, pos))
        index += 1

    raw = corrected_raw
    if DEBUG_MORPHS:
        print(f"\n[DEBUG] ===== 형태소 분석 시작 =====")
        print(f"[DEBUG] 입력 문장: {sentence}")
        print(f"[DEBUG] KKMA 원본 결과: {original_raw}")
        if raw != original_raw:
            print(f"[DEBUG] KKMA 보정 결과: {raw}")
    
    processed = []
    splittable_josa = (
        SUBJECT_TOPIC_PARTICLES
        | ADDITIVE_PARTICLES
        | ADVERBIAL_PARTICLES
        | INDIRECT_PARTICLES
        | OBJECT_COMPLEMENT_PARTICLES
        | {"이", "가", "을", "를", "의", "만"}
    )

    for word, pos in raw:
        if pos in {"NNG", "NNP"}:
            okt_tokens = okt.pos(word, norm=True, stem=True)
            if (
                len(okt_tokens) == 2
                and okt_tokens[0][1] == "Noun"
                and okt_tokens[1][1] == "Josa"
                and okt_tokens[1][0] in splittable_josa
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

    # KKMA 원본 또는 위의 조사 분리 단계에서 내/NNG+가/JKS가 된
    # 주격형만 화자 대명사 `나`로 통일한다. `내 마음` 등의 소유형은 유지한다.
    for index in range(len(processed) - 1):
        word, pos = processed[index]
        if (
            word == "내"
            and _is_noun_token(pos)
            and processed[index + 1] == ("가", "JKS")
        ):
            processed[index] = ("나", "NP")
    
    if DEBUG_MORPHS:
        print(f"[DEBUG] 처리된 결과: {processed}")
        print(f"[DEBUG] ===== 형태소 분석 종료 =====\n")
    
    return processed


def extract_question_object_focus(tokens):
    """현재 답변의 생략 주어로 이어받을 질문의 명시 주어를 찾는다.

    질문의 직접목적어(JKO)는 여기서 반환하지 않는다. 직접목적어는
    ``extract_question_default_object()`` 경로로만 전달하여 중심어가
    주어와 목적어에 동시에 들어가는 역전을 막는다.
    """
    question_subject = None
    clears_person_focus = False
    non_main_indices = _question_non_main_clause_indices(tokens)

    has_meet_predicate = any(word in {"만나", "만나시"} for word, _ in tokens)
    has_explicit_object_marker = any(pos == "JKO" for _, pos in tokens)

    def relative_head_after_subject(subject_index):
        """주격 명사가 관형절 내부라면 그 관형절의 중심 명사를 반환한다."""
        for lookahead in range(subject_index + 2, len(tokens)):
            lookahead_pos = tokens[lookahead][1]
            if lookahead_pos == "ETD":
                for head_index in range(lookahead + 1, len(tokens)):
                    if _is_noun_token(tokens[head_index][1]):
                        return _noun_name_at(tokens, head_index)
                    if tokens[head_index][1] == "SF":
                        break
                return ""
            if lookahead_pos == "SF" or _is_noun_token(lookahead_pos):
                break
        return None

    for index in range(len(tokens) - 1):
        word, pos = tokens[index]
        next_word, next_pos = tokens[index + 1]
        if index in non_main_indices or not _is_noun_token(pos):
            continue

        noun_name = _noun_name_at(tokens, index)
        if not noun_name or any(
            part in QUESTION_GENERIC_NOUNS | {"누"}
            for part in noun_name.split()
        ):
            continue

        if next_pos == "JKS":
            relative_head = relative_head_after_subject(index)
            relative_subject_can_continue = relative_head is None
            temporal_clause_subject = _subject_precedes_temporal_clause_boundary(
                tokens,
                index,
            )
            copular_topic_description = (
                _subject_is_inside_copular_topic_description(tokens, index)
            )
            existential_nonperson_theme = (
                not _is_likely_person(noun_name)
                and any(token_word in {"있", "없"} for token_word, _ in tokens)
            )
            if (
                relative_subject_can_continue
                and not temporal_clause_subject
                and not copular_topic_description
                and not existential_nonperson_theme
            ):
                question_subject = noun_name
        elif (
            next_pos == "JX"
            and next_word in SUBJECT_TOPIC_PARTICLES
            and not has_meet_predicate
            and noun_name not in TEMPORAL_NOUNS
            and noun_name not in positions
            and (
                _is_likely_person(noun_name)
                or has_explicit_object_marker
            )
        ):
            question_subject = noun_name
        elif (
            next_pos == "JX"
            and next_word in ADDITIVE_PARTICLES
            and noun_name not in TEMPORAL_NOUNS
            and noun_name not in positions
            and _is_likely_person(noun_name)
            and has_explicit_object_marker
        ):
            question_subject = noun_name
        elif (
            next_pos == "JKM"
            and any(token_word == "바라" for token_word, _ in tokens)
        ):
            # '손주들에게 바라는 점'의 수혜자는 답변의 희망문 안에서
            # 생략된 행위자가 되는 기존 역할 전환 예외를 유지한다.
            question_subject = noun_name

        if (
            next_pos == 'JX'
            and next_word in SUBJECT_TOPIC_PARTICLES
            and noun_name not in TEMPORAL_NOUNS
            and not _is_likely_person(noun_name)
            and question_subject != noun_name
        ):
            clears_person_focus = True

    return question_subject, clears_person_focus


def extract_question_adnominal_patterns(tokens):
    """Q에 드러난 관형 술어·중심 명사·주어 범위를 구조로 보존한다."""
    patterns = []
    seen = set()
    search_floor = 0
    predicate_positions = {"VV", "VXV", "XSV", "VA", "XSA"}

    for ending_index, (_, ending_pos) in enumerate(tokens):
        if ending_pos == "SF":
            search_floor = ending_index + 1
            continue
        if ending_pos != "ETD":
            continue

        predicate_index = next(
            (
                index
                for index in range(ending_index - 1, search_floor - 1, -1)
                if tokens[index][1] in predicate_positions
            ),
            None,
        )
        if predicate_index is None:
            search_floor = ending_index + 1
            continue

        _, head_index = _find_relative_head(tokens, predicate_index)
        if head_index is None:
            search_floor = ending_index + 1
            continue

        subject_name, _ = _find_explicit_subject(
            tokens,
            search_floor,
            predicate_index,
        )
        if subject_name is not None and any(
            part in QUESTION_GENERIC_NOUNS | {"누"}
            for part in subject_name.split()
        ):
            subject_name = None

        predicate_keys = _adnominal_predicate_match_keys(
            tokens,
            predicate_index,
        )
        signature = (
            tokens[head_index][0],
            predicate_keys,
            subject_name,
        )
        if signature not in seen:
            seen.add(signature)
            patterns.append(
                {
                    "head_token": tokens[head_index][0],
                    "predicate_keys": predicate_keys,
                    "subject": subject_name,
                }
            )
        search_floor = ending_index + 1

    return patterns


def extract_question_required_roles(tokens):
    """의문사가 Q에서 요구하는 비어 있는 문장 성분을 반환한다.

    실제 중심어와 별도로 역할만 전달한다. 예를 들어 `불은 누가 피웠나요`의
    `불`은 질문의 대상이고 `누가`는 답변에서 채워야 할 주어 자리다.
    """
    interrogatives = {"누", "누구", "무엇", "뭐", "어느", "어떤", "몇"}
    required = set()

    for particle_index, (particle_word, particle_pos) in enumerate(tokens):
        if not particle_pos.startswith("J") or particle_index == 0:
            continue

        # `어떤 음식이`, `어느 사람이`처럼 의문 관형사가 명사 앞에
        # 놓이는 형태까지 같은 명사구로 확인한다.
        phrase_words = []
        for index in range(particle_index - 1, max(-1, particle_index - 4), -1):
            word, pos = tokens[index]
            if pos == "SF" or pos.startswith("J") or pos in {
                "VV", "VXV", "VA", "XSV", "XSA", "VCP",
            }:
                break
            phrase_words.append(word)
        if not any(word in interrogatives for word in phrase_words):
            continue

        if particle_pos == "JKS":
            required.add("subject")
        elif particle_pos == "JKO":
            required.add("direct_object")
        elif particle_word in INDIRECT_PARTICLES:
            required.add("indirect_object")
        elif particle_word in OBJECT_COMPLEMENT_PARTICLES:
            required.add("object_complement")

    return required


def extract_question_default_object(tokens):
    """답변에서 생략된 직접목적어를 보충할 질문 대상을 찾는다."""
    candidate = None
    non_main_indices = _question_non_main_clause_indices(tokens)
    asks_for_direct_object = any(
        _is_noun_token(tokens[index][1])
        and tokens[index][0] in QUESTION_GENERIC_NOUNS
        and index + 1 < len(tokens)
        and tokens[index + 1][1] == "JKO"
        for index in range(len(tokens))
    )
    has_meet_predicate = any(
        word in {"만나", "만나시"} for word, _ in tokens
    )
    has_experiencer_predicate = any(
        word in {"생각나", "기억나"} for word, _ in tokens
    )
    for index in range(len(tokens) - 1):
        word, pos = tokens[index]
        if not _is_noun_token(pos):
            continue

        noun_name = _noun_name_at(tokens, index)
        next_word, next_pos = tokens[index + 1]
        # 예/아니오 질문의 관형절 안에서 을/를로 명시된 대상은 A의
        # 생략 목적어 후보로 사용할 수 있다. 다만 Q가 `무엇을`로 새
        # 목적어를 요구한다면 관형절의 이전 목적어를 넘기지 않는다.
        if (
            index in non_main_indices
            and not (next_pos == "JKO" and not asks_for_direct_object)
        ):
            continue
        if (
            noun_name in TEMPORAL_NOUNS
            or noun_name in QUESTION_GENERIC_NOUNS
        ):
            continue

        if _is_likely_person(noun_name):
            # `배우자는 어떻게 만났나요`, `영수 선배도 생각나세요`에서
            # 사람 중심어는 답변 주어가 아니라 생략된 만남·기억 대상이다.
            if noun_name in {"사람", "사람들", "분", "누구"}:
                continue
            if next_pos == "JKO":
                candidate = noun_name
            elif has_experiencer_predicate and (
                next_pos == "JKS"
                or (
                    next_pos == "JX"
                    and next_word in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
                )
            ):
                candidate = noun_name
            elif (
                has_meet_predicate
                and next_pos == "JX"
                and next_word in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
            ):
                candidate = noun_name
            continue

        if next_pos == 'JKO' or (
            next_pos == 'JX'
            and next_word in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
            and not asks_for_direct_object
        ):
            candidate = noun_name

    return candidate


def question_object_predicate_scope(tokens, object_name):
    """Q의 목적어 후보와 직접 연결된 것으로 볼 수 있는 술어 범위를 반환한다.

    기존 의미역 목록에 없는 동사라도 Q와 A에서 같은 술어가 확인되면
    Q의 조사 근거로 A의 빈 목적어를 채울 수 있게 한다. 사람 회상·만남
    질문은 기존의 제한된 장기 기억 범위를 유지한다.
    """
    if object_name is None:
        return None
    if _is_likely_person(object_name) and any(
        word in {"만나", "만나시"} for word, _ in tokens
    ):
        return frozenset({"만나다"})
    if _is_likely_person(object_name) and any(
        word in {"생각나", "기억나"} for word, _ in tokens
    ):
        return frozenset({"기억하다", "생각하다", "떠올리다"})

    predicates = set()
    for index, (word, pos) in enumerate(tokens):
        if pos not in {"VV", "VXV", "XSV"}:
            continue
        if pos == "VXV":
            continue
        base_verb = _normalize_relation_verb(tokens, index)
        if base_verb not in {"하다", "되다", "있다"}:
            predicates.add(base_verb)
    return frozenset(predicates) if predicates else None


def extract_question_indirect_object(tokens):
    """질문의 에게/한테/께 대상을 다음 답변의 간접목적어로 전달한다."""
    candidate = None
    non_main_indices = _question_non_main_clause_indices(tokens)
    for index in range(len(tokens) - 1):
        word, pos = tokens[index]
        if index in non_main_indices or not _is_noun_token(pos):
            continue
        particle_word, particle_pos = tokens[index + 1]
        noun_name = _noun_name_at(tokens, index)
        if (
            particle_word in INDIRECT_PARTICLES
            and particle_pos.startswith("J")
            and _is_likely_person(noun_name)
        ):
            candidate = noun_name
    return candidate


QUESTION_EXPERIENCER_PREDICATE_STEMS = {"떠오르", "생각나", "기억나"}


def extract_question_default_experiencer(tokens):
    """회상 질문에서 생략된 경험자를 찾고, 없으면 답변 화자로 복원한다.

    `무엇이 떠오르세요`의 주격 명사는 떠오르는 내용이므로 경험자로
    사용하지 않는다. `철수는`, `아이에게`처럼 사람 주제·간접격이
    술어 앞에 명시된 경우에만 그 인물을 경험자로 인정한다.
    타동사 `떠올리다`는 일반 주어·목적어 분석을 유지한다.
    """
    predicate_indices = [
        index
        for index, (word, _) in enumerate(tokens)
        if word in QUESTION_EXPERIENCER_PREDICATE_STEMS
    ]
    if not predicate_indices:
        return None

    predicate_index = predicate_indices[-1]
    explicit_experiencer = None
    for index in range(predicate_index):
        if not _is_noun_token(tokens[index][1]) or index + 1 >= len(tokens):
            continue
        noun_name = _noun_name_at(tokens, index)
        particle_word, particle_pos = tokens[index + 1]
        if not _is_likely_person(noun_name):
            continue
        if (
            particle_word in INDIRECT_PARTICLES
            and particle_pos.startswith("J")
        ):
            explicit_experiencer = noun_name
        elif (
            particle_pos == "JX"
            and particle_word in SUBJECT_TOPIC_PARTICLES
        ):
            explicit_experiencer = noun_name

    return explicit_experiencer or "나"


def question_prefers_speaker_subject(tokens):
    """높임형 질문이나 사람 수혜자 질문은 답변 화자의 행동을 묻는 것으로 본다."""
    # '손주들에게 바라는 점'은 화자의 바람 안에서 손주들이 행동 주체가 된다.
    if any(word == "바라" for word, _ in tokens):
        return False

    if extract_question_indirect_object(tokens) is not None:
        return True

    for index, (word, pos) in enumerate(tokens):
        if pos.startswith("EC") and (word == "면" or word.endswith("으면")):
            has_later_predicate = any(
                later_pos in {"VV", "VXV", "VA", "XSV", "XSA", "VCP"}
                for _, later_pos in tokens[index + 1:]
            )
            if not has_later_predicate:
                return True

    for word, pos in tokens:
        if pos == "EPH" or word in {"시", "으시"}:
            return True
        if any(ending in word for ending in {"세요", "으세요", "실래요", "셨나요", "셨어요"}):
            return True
    return False


def question_has_nonperson_center(tokens):
    """사람 중심어 대신 명시된 사물/장소 주제나 주어가 있는지 확인한다."""
    non_main_indices = _question_non_main_clause_indices(tokens)
    for index in range(len(tokens) - 1):
        word, pos = tokens[index]
        if index in non_main_indices or not _is_noun_token(pos):
            continue
        noun_name = _noun_name_at(tokens, index)
        next_word, next_pos = tokens[index + 1]
        if (
            noun_name not in TEMPORAL_NOUNS
            and not _is_likely_person(noun_name)
            and (
                next_pos == "JKS"
                or (
                    next_pos == "JX"
                    and next_word in SUBJECT_TOPIC_PARTICLES
                )
            )
        ):
            return True
    return False


def _verb_is_followed_by_conditional(tokens, verb_index):
    """현재 술어 뒤에 -면/-으면 조건절 경계가 있는지 확인한다."""
    for word, pos in tokens[verb_index + 1:]:
        if pos == "SF" or pos in {"VV", "VXV", "XSV"}:
            return False
        if pos.startswith("EC") and (word == "면" or word.endswith("으면")):
            return True
    return False


ADJECTIVE_AUXILIARIES = {"같다"}
EXPERIENCER_ADJECTIVES = {"힘들다", "허전하다", "반갑다"}


def _find_adjective_head(tokens, adjective_index):
    """관형형 형용사가 직접 꾸미는 다음 명사를 찾는다."""
    etd_index = None
    saw_tense_ending = False
    for index in range(adjective_index + 1, len(tokens)):
        word, pos = tokens[index]
        if pos == "ETD":
            etd_index = index
            break
        if pos == "SF" or pos in {"VV", "VXV", "VCP"}:
            return None
        # '말은 적었지만 꼼꼼한 사람'의 서술형 형용사가 뒤 관형절의
        # ETD를 자기 어미로 오인하지 않도록 시제 뒤 연결어미에서 멈춘다.
        # '맛있었던 음식'의 시제+ETD와 기존 병렬 형용사 처리는 유지한다.
        if pos.startswith("EP"):
            saw_tense_ending = True
            continue
        if pos.startswith("EF"):
            return None
        if pos.startswith("EC") and saw_tense_ending:
            return None
        # '고맙고 따뜻한 마음'처럼 마지막 형용사에만 관형형 어미가
        # 붙은 병렬 수식은 앞 형용사도 같은 명사를 꾸민다.
        if pos in {"VA", "XSA", "XSV"}:
            continue
        if _is_noun_token(pos):
            next_pos = tokens[index + 1][1] if index + 1 < len(tokens) else None
            if next_pos not in {"XSA", "XSV", "VA"}:
                return None

    if etd_index is None:
        return None

    for index in range(etd_index + 1, len(tokens)):
        _, pos = tokens[index]
        if _is_noun_token(pos):
            return _noun_name_at(tokens, index)
        if pos == "SF" or pos in {"VV", "VXV", "VA", "XSA", "XSV", "VCP"}:
            break
    return None


def _find_adjective_head_index(tokens, adjective_index, head_noun):
    """형용사 관형형 중심 명사의 토큰 위치를 반환한다."""
    if head_noun is None:
        return None
    saw_etd = False
    for index in range(adjective_index + 1, len(tokens)):
        _, pos = tokens[index]
        if pos == "ETD":
            saw_etd = True
            continue
        if saw_etd and _is_noun_token(pos):
            if _noun_name_at(tokens, index) == head_noun:
                return index
        if saw_etd and (
            pos == "SF"
            or pos in {"VV", "VXV", "VA", "XSA", "XSV", "VCP"}
        ):
            break
    return None


def _is_adverbial_adjective(tokens, adjective_index):
    """'-게 + 동사'처럼 명사의 속성이 아닌 동작 방식을 꾸미는 형용사인지 확인한다."""
    if adjective_index + 1 >= len(tokens):
        return False
    next_word, next_pos = tokens[adjective_index + 1]
    return next_word == "게" and next_pos.startswith("EC")


def _normalize_adjective(tokens, adjective_index):
    """KKMA가 나눈 형용사와 형용사 파생 표현을 관계 저장용 기본형으로 복원한다."""
    word, pos = tokens[adjective_index]

    if pos == "XSA" and adjective_index > 0:
        root, root_pos = tokens[adjective_index - 1]
        if root_pos in {"XR", "NNG"}:
            return root + "하다"

    if (
        pos == "XSV"
        and adjective_index > 0
        and tokens[adjective_index - 1][0] in ADJECTIVE_HADA_ROOTS
    ):
        return tokens[adjective_index - 1][0] + "하다"

    if pos == "VV" and word == "해지" and adjective_index > 0:
        root, root_pos = tokens[adjective_index - 1]
        if root_pos == "XR":
            return root + "하다"

    if pos == "VA":
        if (
            word == "차지"
            and adjective_index > 0
            and tokens[adjective_index - 1][0] == "활기"
        ):
            return "활기차다"
        return word if word.endswith("다") else word + "다"

    return None


def _okt_confirms_hada_adjective(root):
    """KKMA의 명사+XSV 중 Okt가 전체를 형용사로 보는 경우를 확인한다."""
    if root not in _OKT_HADA_ADJECTIVE_CACHE:
        try:
            okt_tokens = okt.pos(root + "하다", norm=True, stem=True)
        except Exception:
            okt_tokens = []
        _OKT_HADA_ADJECTIVE_CACHE[root] = bool(
            len(okt_tokens) == 1
            and okt_tokens[0][1] == "Adjective"
        )
    return _OKT_HADA_ADJECTIVE_CACHE[root]


def _is_jko_bounded_adjective_predicate(tokens, index, start):
    """목적격 경계가 있을 때만 Okt의 명사+하다 형용사 판정을 보조로 쓴다."""
    if _is_adjective_predicate(tokens, index):
        return True
    if (
        tokens[index][1] != "XSV"
        or index == 0
        or not _verb_has_following_etd(tokens, index)
        or not _okt_confirms_hada_adjective(tokens[index - 1][0])
    ):
        return False
    return any(
        particle_pos == "JKO"
        for _, particle_pos in tokens[start:index]
    )


def _is_adjective_predicate(tokens, index):
    word, pos = tokens[index]
    if pos in {"VA", "XSA"}:
        return True
    if (
        pos == "XSV"
        and index > 0
        and tokens[index - 1][0] in ADJECTIVE_HADA_ROOTS
    ):
        return True
    return (
        pos == "VV"
        and word == "해지"
        and index > 0
        and tokens[index - 1][1] == "XR"
    )


def _adjective_clause_start(tokens, adjective_index):
    """현재 형용사 앞의 가장 가까운 절 경계를 찾는다."""
    for index in range(adjective_index - 1, -1, -1):
        word, pos = tokens[index]
        if pos == "SF":
            return index + 1
        if pos.startswith("EC") and word not in {"게"}:
            return index + 1
    return 0


def _adnominal_adjective_clause_start(
    tokens,
    adjective_index,
    head_index=None,
):
    """형용사·동사가 공유하는 관형형 경계 규칙으로 시작점을 찾는다."""
    clause_start = _adjective_clause_start(tokens, adjective_index)
    for index in range(adjective_index - 1, clause_start - 1, -1):
        if tokens[index][1] == "ETD":
            clause_start = index + 1
            break

    complement_start = _adnominal_object_complement_start(
        tokens,
        clause_start,
        adjective_index,
        head_index,
    )
    return (
        complement_start
        if complement_start is not None
        else clause_start
    )


def _find_bare_adnominal_subject(tokens, start, adjective_index):
    """`손재주 좋은`, `키 큰`의 형용사 직전 무조사 주어를 찾는다."""
    previous = adjective_index - 1
    if (
        previous < start
        or not _is_noun_token(tokens[previous][1])
        or tokens[adjective_index][1] in {"XSA", "XSV"}
    ):
        return None

    noun_name = _noun_name_at(tokens, previous)
    if noun_name in (
        TEMPORAL_NOUNS
        | QUESTION_GENERIC_NOUNS
        | NON_OBJECT_NOUNS
        | MEASURE_NOUNS
    ):
        return None
    return noun_name


def _find_adjective_subject(tokens, start, end, head_noun=None):
    """형용사 절 안의 주격·주제·보조사 명사를 찾는다."""
    candidate = None
    for index in range(start, end):
        word, pos = tokens[index]
        if not _is_noun_token(pos) or index + 1 >= len(tokens):
            continue
        next_word, next_pos = tokens[index + 1]
        if (
            next_pos == "JKS"
            or (
                next_pos == "JX"
                and next_word in SUBJECT_TOPIC_PARTICLES | ADDITIVE_PARTICLES
            )
        ):
            noun_name = _noun_name_at(tokens, index)
            # `지금도 고마운 사람`처럼 시간 명사+보조사가 앞에 있고
            # 관형형 형용사가 뒤 명사를 직접 꾸미면 시간 표현은 부사어다.
            # `시간이 소중하다`와 같은 명시적 주격 주어는 그대로 둔다.
            if (
                head_noun is not None
                and noun_name in TEMPORAL_NOUNS
                and next_pos == "JX"
            ):
                continue
            candidate = noun_name

    # KKMA가 '산도'처럼 보조사를 명사에 붙여 태깅한 경우 보정한다.
    if candidate is None and end > start:
        word, pos = tokens[end - 1]
        if _is_noun_token(pos) and len(word) > 1 and word.endswith("도"):
            candidate = word[:-1]
    return candidate


def _has_companion_phrase(tokens, start, end):
    """'아주머니와 친하다'에서 아주머니는 형용사의 주어가 아닌 동반자다."""
    for index in range(start, end - 1):
        if not _is_noun_token(tokens[index][1]):
            continue
        particle, particle_pos = tokens[index + 1]
        if particle in {"와", "과", "랑", "이랑"} and particle_pos in {"JC", "JKM"}:
            return True
    return False


def _is_auxiliary_adjective_usage(tokens, adjective_index, adjective):
    """희망·추측 구문의 보조 형용사는 명사의 고정 속성에서 제외한다."""
    if adjective in ADJECTIVE_AUXILIARIES:
        return True
    if adjective != "좋다":
        return False

    has_wish_ending = any(
        word == "겠" and pos.startswith("EP")
        for word, pos in tokens[adjective_index + 1:]
    )
    has_previous_conditional = any(
        pos.startswith("EC") and (word == "면" or word.endswith("으면"))
        for word, pos in tokens[:adjective_index]
    )
    return has_wish_ending and has_previous_conditional


def assign_adjectives_to_nouns(
    tokens,
    noun_dict,
    default_subject=None,
    focus_entity=None,
    default_object=None,
    default_experiencer=None,
):
    """원본 imagine 형식을 유지하며 형용사를 의미상 대상 명사에 저장한다."""
    bindings = []
    previous_predicate_target = None

    for index in range(len(tokens)):
        if not _is_adjective_predicate(tokens, index):
            continue

        adjective = _normalize_adjective(tokens, index)
        if adjective is None or _is_auxiliary_adjective_usage(tokens, index, adjective):
            continue

        # `빠르게 지나가다`, `곱게 차려입다`의 VA+게는 명사의 속성이
        # 아니라 뒤 동작의 방식을 나타낸다. 주어나 관형 대상보다 먼저
        # 판정해야 `(산 빠르다)` 같은 잘못된 명사·형용사 관계가 생기지 않는다.
        if _is_adverbial_adjective(tokens, index):
            continue

        head_noun = _find_adjective_head(tokens, index)
        head_index = _find_adjective_head_index(
            tokens,
            index,
            head_noun,
        )
        clause_start = (
            _adnominal_adjective_clause_start(
                tokens,
                index,
                head_index=head_index,
            )
            if head_noun is not None
            else _adjective_clause_start(tokens, index)
        )
        bare_subject = (
            _find_bare_adnominal_subject(tokens, clause_start, index)
            if head_noun is not None
            else None
        )
        explicit_subject = (
            _find_local_adnominal_adjective_subject(
                tokens,
                clause_start,
                index,
            )[0]
            if head_noun is not None
            else _find_adjective_subject(
                tokens,
                clause_start,
                index,
                head_noun=head_noun,
            )
        )
        if bare_subject is not None:
            target_name = bare_subject
        elif explicit_subject is not None:
            target_name = explicit_subject
        elif head_noun is not None:
            target_name = head_noun
        else:
            target_name = None
            # 회상 질문의 경험자와 감정 형용사의 주체를 오래 기억한
            # 질문 목적어보다 먼저 사용한다. 관형형은 위의 head_noun
            # 분기에서 계속 중심 명사에 연결된다.
            if adjective in EXPERIENCER_ADJECTIVES:
                experiencer = default_experiencer or default_subject
                if experiencer is not None:
                    target_name = experiencer.noun
            is_coordinated = (
                clause_start > 0
                and tokens[clause_start - 1][0] == "고"
                and tokens[clause_start - 1][1].startswith("EC")
            )
            if (
                target_name is None
                and is_coordinated
                and previous_predicate_target is not None
            ):
                target_name = previous_predicate_target
            if target_name is None and _has_companion_phrase(tokens, clause_start, index):
                if default_subject is not None:
                    target_name = default_subject.noun
            if (
                target_name is None
                and default_object is not None
                and default_object.noun not in QUESTION_GENERIC_NOUNS
            ):
                target_name = default_object.noun
            if target_name is None and focus_entity is not None:
                target_name = focus_entity.noun
            if target_name is None and default_subject is not None:
                target_name = default_subject.noun
            if target_name is None:
                target_name = "나"

        target = _get_relation_noun(noun_dict, target_name)
        if adjective not in target.adjectives:
            target.adjectives.append(adjective)
        bindings.append((target_name, adjective))
        if head_noun is None:
            previous_predicate_target = target_name

    return bindings


def _attach_subjectless_adnominal_verbs_as_attributes(
    tokens,
    noun_dict,
    bindings,
    default_object=None,
    question_required_roles=None,
    question_adnominal_patterns=None,
):
    """명시적 주어가 없는 동사 관형형을 중심 명사의 상태로 보존한다.

    `젖은 흙`, `마른 그릇`, `찢어진 그물`처럼 형태소 분석기가 상태를
    VV 관형형으로 분석하면 기존 형용사 처리에서는 시각 속성이 사라진다.
    관형절 범위 안에 이/가·은/는·도 주어가 직접 드러나지 않은 경우에만
    동사 원형을 뒤쪽 중심 명사에 붙인다. `철수가 만든 음식`처럼 주어가
    명시된 관형절은 기존 RelativeClause 관계만 사용해 동작과 속성을
    중복 생성하지 않는다.
    """
    corrected_bindings = list(bindings)
    seen = set(corrected_bindings)

    reserved_main_object_indices = _question_confirmed_main_object_indices(
        tokens,
        default_object,
        question_required_roles=question_required_roles,
    )
    raw_spans = _adnominal_clause_spans(
        tokens,
        reserved_main_role_indices=reserved_main_object_indices,
    )
    reserved_main_subject_indices = _question_guided_main_subject_indices(
        tokens,
        raw_spans,
        question_required_roles=question_required_roles,
        question_adnominal_patterns=question_adnominal_patterns,
    )
    spans = _adnominal_clause_spans(
        tokens,
        reserved_main_subject_indices=reserved_main_subject_indices,
        reserved_main_role_indices=reserved_main_object_indices,
    )
    spans = _mark_question_confirmed_adnominal_spans(
        tokens,
        spans,
        question_adnominal_patterns,
    )

    for span in spans:
        predicate_index = span["predicate"]
        predicate_word, predicate_pos = tokens[predicate_index]
        if predicate_pos not in {"VV", "VXV", "XSV"}:
            continue
        if _is_adjective_predicate(tokens, predicate_index):
            continue

        head_name, head_index = _find_relative_head(tokens, predicate_index)
        if head_name is None or head_index is None:
            continue

        excluded_indices = set()
        if (
            predicate_pos == "XSV"
            and predicate_index > span["start"]
            and _is_noun_token(tokens[predicate_index - 1][1])
        ):
            excluded_indices.add(predicate_index - 1)
        explicit_subject, _ = _find_explicit_subject(
            tokens,
            span["start"],
            predicate_index,
            excluded_indices=(
                excluded_indices | reserved_main_subject_indices
            ),
        )
        if explicit_subject is not None:
            continue

        attribute = _normalize_relation_verb(
            tokens,
            predicate_index,
            explicit_subject_name=None,
        )
        if not attribute:
            continue

        target = _get_relation_noun(noun_dict, head_name)
        if attribute not in target.adjectives:
            target.adjectives.append(attribute)
        binding = (head_name, attribute)
        if binding not in seen:
            seen.add(binding)
            corrected_bindings.append(binding)

    return corrected_bindings


def _realign_adnominal_adjectives(tokens, noun_dict, relations, bindings):
    """본절 주어로 잘못 붙은 관형형 형용사를 직접목적어 명사로 되돌린다.

    `관객들이 큰 박수를 보내다`에서 형태만 보면 관객들이/박수 중 어느
    쪽이 `크다`의 대상인지 모호하다. 생성된 Relation이 앞 명사를 주어,
    뒤 관형 명사를 직접목적어로 확정한 경우에만 뒤 명사로 재정렬한다.
    따라서 `목소리가 따뜻한 사람`처럼 내포절의 명시 주어는 유지된다.
    """
    all_relations = []

    def collect(relation):
        all_relations.append(relation)
        for reason_clause in getattr(relation, "reason_clauses", []):
            collect(reason_clause)

    for relation in relations:
        collect(relation)

    candidates = {}
    for index in range(len(tokens)):
        if not _is_adjective_predicate(tokens, index):
            continue
        adjective = _normalize_adjective(tokens, index)
        if adjective is None:
            continue
        head_noun = _find_adjective_head(tokens, index)
        head_index = _find_adjective_head_index(
            tokens,
            index,
            head_noun,
        )
        clause_start = (
            _adnominal_adjective_clause_start(
                tokens,
                index,
                head_index=head_index,
            )
            if head_noun is not None
            else _adjective_clause_start(tokens, index)
        )
        if (
            head_noun is not None
            and _find_bare_adnominal_subject(tokens, clause_start, index)
            is not None
        ):
            continue
        explicit_subject = (
            _find_local_adnominal_adjective_subject(
                tokens,
                clause_start,
                index,
            )[0]
            if head_noun is not None
            else _find_adjective_subject(
                tokens,
                clause_start,
                index,
                head_noun=head_noun,
            )
        )
        if (
            explicit_subject is None
            or head_noun is None
            or explicit_subject == head_noun
        ):
            continue

        # 본절 주어 바로 뒤에 관형형 형용사가 오면 `농부가 넓은
        # 마당에서 ...`의 농부를 형용사절 주어로 오인할 수 있다.
        # 생성된 본절에서 앞 명사는 주어, 관형 대상은 직접목적어 또는
        # 부사격 문맥으로 확인되는 경우에만 수식 대상을 뒤 명사로 옮긴다.
        role_confirms_head = any(
            explicit_subject in {item.noun for item in relation.subjects}
            and (
                head_noun in {item.noun for item in relation.direct_objects}
                or head_noun in {
                    getattr(item, "noun", str(item))
                    for item in relation.context_nouns
                }
            )
            for relation in all_relations
        )
        if role_confirms_head:
            candidates.setdefault(
                (explicit_subject, adjective),
                set(),
            ).add(head_noun)

    redirects = {
        source_binding: next(iter(head_names))
        for source_binding, head_names in candidates.items()
        if len(head_names) == 1
    }
    if not redirects:
        return bindings

    corrected_bindings = []
    seen = set()
    for noun_name, adjective in bindings:
        noun_name = redirects.get((noun_name, adjective), noun_name)
        binding = (noun_name, adjective)
        if binding in seen:
            continue
        seen.add(binding)
        corrected_bindings.append(binding)

    for (source_name, adjective), target_name in redirects.items():
        source = noun_dict.get(source_name)
        if (
            source is not None
            and (source_name, adjective) not in corrected_bindings
        ):
            source.adjectives = [
                item for item in source.adjectives if item != adjective
            ]
        target = _get_relation_noun(noun_dict, target_name)
        if adjective not in target.adjectives:
            target.adjectives.append(adjective)

    return corrected_bindings


def _link_object_complements_to_direct_objects(
    noun_dict,
    relations,
    adjective_bindings,
):
    """목적격보어 imagine 객체를 그것이 설명하는 직접목적어에 연결한다.

    `영수 선배를 고마운 사람으로 기억하다`에서 `사람`은 새 인물이
    아니라 `영수 선배`의 분류·설명이다. 직접목적어가 하나로 확정된
    Relation에 한해 complement.target으로 실제 객체를 가리키고,
    목적격보어의 관형형 형용사는 직접목적어의 속성으로 옮긴다.
    """
    complement_targets = {}

    def collect(relation):
        direct_objects = getattr(relation, "direct_objects", [])
        object_complements = getattr(relation, "object_complements", [])
        if len(direct_objects) == 1:
            target = direct_objects[0]
            for complement in object_complements:
                if complement.noun == target.noun:
                    continue
                complement_targets.setdefault(complement.noun, {})[
                    target.noun
                ] = target
        for reason_clause in getattr(relation, "reason_clauses", []):
            collect(reason_clause)

    for relation in relations:
        collect(relation)

    redirects = {}
    for complement_name, targets in complement_targets.items():
        if len(targets) != 1:
            continue
        target = next(iter(targets.values()))
        complement = noun_dict.get(complement_name)
        if complement is None:
            continue
        if (
            complement.target is not None
            and getattr(complement.target, "noun", None) != target.noun
        ):
            continue
        complement.target = target
        redirects[complement_name] = target.noun

    if not redirects:
        return adjective_bindings

    corrected_bindings = []
    seen = set()
    for noun_name, adjective in adjective_bindings:
        target_name = redirects.get(noun_name, noun_name)
        if target_name != noun_name:
            source = noun_dict.get(noun_name)
            if source is not None:
                source.adjectives = [
                    item for item in source.adjectives if item != adjective
                ]
            target = _get_relation_noun(noun_dict, target_name)
            if adjective not in target.adjectives:
                target.adjectives.append(adjective)
        binding = (target_name, adjective)
        if binding in seen:
            continue
        seen.add(binding)
        corrected_bindings.append(binding)
    return corrected_bindings


def _record_entity_tree_mentions(
    entity_tree,
    noun_objects,
    question_entities=None,
    question_reference_objects=None,
    turn_index=None,
):
    """Q의 후보와 A의 객체를 병합 없이 트리에 누적한다."""
    created = []
    question_reference_by_name = {
        re.sub(r"\s+", "", reference.noun): reference
        for reference in question_reference_objects or []
        if reference is not None and hasattr(reference, "noun")
    }
    seen_question_names = set()
    for entity in question_entities or []:
        name = getattr(entity, "noun", str(entity))
        compact_name = re.sub(r"\s+", "", name)
        if not compact_name or compact_name in seen_question_names:
            continue
        seen_question_names.add(compact_name)
        entity_reference = (
            entity
            if hasattr(entity, "noun")
            else question_reference_by_name.get(compact_name)
        )
        node = entity_tree.add_mention(
            name,
            turn_index=turn_index,
            source="question",
            adjectives=getattr(entity_reference, "adjectives", []),
            object_ref=entity_reference,
        )
        if node is not None:
            created.append(node)
            if entity_reference is not None:
                entity_reference.entity_mention_ids.append(node.node_id)

    for noun_object in noun_objects or []:
        node = entity_tree.add_mention(
            noun_object.noun,
            turn_index=turn_index,
            source="answer",
            adjectives=noun_object.adjectives,
            object_ref=noun_object,
        )
        if node is None:
            continue
        created.append(node)
        noun_object.entity_mention_ids.append(node.node_id)
    return created


def _current_entity_ids(entity_tree, noun_object, mentions, source):
    """현재 턴에서 특정 imagine 객체가 만든 발생 노드만 반환한다."""
    return {
        entity_tree.canonical_id(node.node_id)
        for node in mentions or []
        if (source is None or node.source == source)
        and node.object_ref is noun_object
    }


def _apply_entity_identity_to_relations(
    entity_tree,
    relations,
    mentions,
    default_subject=None,
):
    """확정된 Q→A 객체 판정을 경험 관계의 역전에만 제한 적용한다.

    Q의 일반 대상과 A의 구체 대상이 같은 객체로 확정됐는데 분석 결과가
    ``(구체 대상 기억나다 일반 대상)``처럼 자기 자신을 주어와 목적어에
    동시에 둔 경우를 바로잡는다. 명시 객체가 여러 개이거나 일반 행동
    관계인 경우에는 기존 관계를 건드리지 않는다.
    """
    if default_subject is None:
        return []

    corrections = []
    identity_experiencer_verbs = {
        "기억나다",
        "생각나다",
        "떠오르다",
    }

    def visit(relation):
        if (
            relation.verb in identity_experiencer_verbs
            and len(getattr(relation, "direct_objects", [])) == 1
        ):
            subject = relation.subjects[0]
            direct_object = relation.direct_objects[0]
            object_ids = _current_entity_ids(
                entity_tree,
                direct_object,
                mentions,
                None,
            )
            question_ids = {
                entity_tree.canonical_id(node.node_id)
                for node in mentions or []
                if node.source == "question"
            }
            resolved_object_ids = object_ids & question_ids
            if resolved_object_ids:
                canonical_object = entity_tree.canonical_object(direct_object)
                old_subject = subject.noun
                old_object = direct_object.noun
                new_subject = subject

                # 구체 대상이 잘못 경험자로도 선택된 역전만 화자로 복원한다.
                if len(getattr(relation, "subjects", [])) == 1:
                    subject_ids = _current_entity_ids(
                        entity_tree,
                        subject,
                        mentions,
                        "answer",
                    )
                    if subject_ids & resolved_object_ids:
                        new_subject = default_subject

                relation.subject = new_subject
                relation.subjects = [new_subject]
                relation.obj = canonical_object
                relation.direct_objects = [canonical_object]
                _refresh_relation_displays(relation)
                if (
                    old_subject != new_subject.noun
                    or old_object != canonical_object.noun
                    or direct_object is not canonical_object
                ):
                    corrections.append(
                        {
                            "verb": relation.verb,
                            "from_subject": old_subject,
                            "from_object": old_object,
                            "to_subject": new_subject.noun,
                            "to_object": canonical_object.noun,
                        }
                    )

        for reason_clause in getattr(relation, "reason_clauses", []):
            visit(reason_clause)

    for relation in relations or []:
        visit(relation)
    return corrections


def extract_nouns_adjectives_verbs(
    sentence,
    tokens=None,
    default_subject=None,
    focus_entity=None,
    default_object=None,
    default_object_predicates=None,
    default_indirect_object=None,
    remembered_focus_entity=None,
    default_experiencer=None,
    relation_history=None,
    question_required_roles=None,
    question_adnominal_patterns=None,
    entity_tree=None,
    entity_turn_index=None,
    question_entities=None,
):
    """한 문장에서 명사, 형용사, 동사를 추출하여 동사 관계를 생성
    
    Args:
        sentence: 분석할 문장
        tokens: 형태소 분석 결과 (None이면 자동 분석)
        default_subject: 화자 등 기본 주어
        focus_entity: 현재 Q에서 답변 주어로 이어받은 사람 대상
        default_object: 현재 Q에서 답변 목적어로 이어받은 비인물 대상
        default_object_predicates: 기억 목적어를 적용할 수 있는 동사 집합
        default_indirect_object: 현재 Q에서 답변 수혜자로 이어받은 사람 대상
        remembered_focus_entity: 이전 Q들에서 기억하고 있는 약한 대화 중심 인물
        default_experiencer: 회상·감정 질문에서 답변의 생략된 경험자
        relation_history: 이전 답변들에서 생성된 Relation 목록
        question_required_roles: 현재 Q가 A에서 요구한 주어·목적어 역할 집합
        question_adnominal_patterns: Q의 관형 술어·중심 명사·주어 범위
        entity_tree: 턴 사이에서 객체 발생을 누적할 EntityTree
        entity_turn_index: 발생 노드에 기록할 현재 턴 번호
        question_entities: 현재 Q에서 확인된 객체 후보 목록
    """
    # [Level 3] PII 마스킹 및 위험어 차단 로직 적용
    sentence = sentence.strip()
    sentence = mask_pii(sentence)
    
    if DEBUG_MORPHS:
        print(f"[DEBUG] predicate_nouns 리스트: {predicate_nouns}")
    
    # 형태소 분석 (tokens가 없을 때만 수행)
    if tokens is None:
        tokens = postprocess_kkma_pos(sentence)

    if entity_tree is None:
        entity_tree = EntityTree()
    elif not isinstance(entity_tree, EntityTree):
        raise TypeError("entity_tree must be an EntityTree instance")
    
    # [Level 3] 위험어 필터링 (분석 결과 기반)
    if not is_safe_tokens(tokens):
        return {"error": "DANGER_WORD_DETECTED"}
    
    noun_dict = {}
    registered_noun_phrases = _register_noun_phrase_objects(
        sentence,
        tokens,
        noun_dict,
    )
    if DEBUG_MORPHS:
        print(f"[DEBUG] 선등록 명사구: {registered_noun_phrases}")
    current_adjectives = []
    noun_stack = []  # 조사로 역할이 확정되지 않은 명사만 저장
    noun_stack_history = []  # noun_stack에 들어간 명사 전체 이력
    current_subject = None  
    
    # default_subject가 있으면 술어명사와 위치 연결에 사용할 현재 주어로 설정
    if default_subject is not None:
        current_subject = default_subject
        if DEBUG_MORPHS:
            print(f"[DEBUG] default_subject 초기화: '{default_subject.noun}'")
    
    all_adjectives = []
    all_verbs = []
    all_positions = []
    
    i = 0
    while i < len(tokens):
        word, pos = tokens[i]
        
        # [Level 1] 형용사 수집 및 원형 복원
        # 관형형은 다음 명사에 넘기고, 서술형은 아래의 의미 기반 후처리에서
        # 명시 주어/Q 중심 명사/화자 순서로 연결한다.
        if pos in ['VA'] or word.endswith("색") or word.endswith("빛"):  
            base_word = get_base_form(word, pos) if pos == 'VA' else word
            all_adjectives.append(base_word)
            if _find_adjective_head(tokens, i) is not None:
                current_adjectives.append(base_word)
            if DEBUG_MORPHS:
                print(f"[DEBUG] 형용사 추출: '{word}' -> 원형: '{base_word}'")
        
        # ETD 관계는 아래 generate_relations()에서 한 번만 처리한다.
        # 여기서는 관형형 앞에서 모은 형용사를 다음 명사까지 유지한다.
        elif pos == 'ETD':
            pass
        
        # 명사 처리
        elif pos.startswith('N'):
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
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 명사 등록: '{word}'")
                else:
                    # [수정] 형용사 중복 제외하고 추가
                    for adj in current_adjectives:
                        if adj not in noun_dict[word].adjectives:
                            noun_dict[word].adjectives.append(adj)
                # 역할이 확정되기 전까지는 여기서 스택에 넣지 않음
            else:
                # [변경] 위치 명사: 스택에서 pop된 명사에 위치 정보 저장
                if noun_stack:
                    popped_noun = noun_stack.pop()
                    # 위치 정보 저장 (현재 주어 기준)
                    subject_for_position = current_subject if current_subject is not None else default_subject
                    if subject_for_position is not None:
                        position_info = (popped_noun.noun, word)
                        if position_info not in subject_for_position.position:
                            all_positions.append(position_info)
                            subject_for_position.position.append(position_info)
                            if DEBUG_MORPHS:
                                print(f"[DEBUG] 위치 저장(명사 단계): '{subject_for_position.noun}'에 {position_info} 추가")
                    else:
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 위치 명사 감지: '{word}' (주어 없음, 위치 저장 스킵)")
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 위치 명사 감지: '{word}' -> 스택에서 '{popped_noun.noun}' 제거")
                else:
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 위치 명사 감지: '{word}' (스택이 비어있음)")
            
            # 다음 토큰이 있으면 조사 확인
            if i + 1 < len(tokens):
                next_word, next_pos = tokens[i + 1]
                
                if next_pos == 'JKO':  # 목적어 조사
                    # [수정] 위치명사는 목적어로 등록하지 않음 (명사 단계에서 이미 처리됨)
                    if word in positions:
                        current_adjectives = []
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 위치명사 JKO: '{word}' (목적어로 등록 안함, 위치는 명사 단계에서 처리됨)")
                    else:
                        # [수정] 일반 명사는 목적어로 등록
                        if word not in noun_dict:
                            new_noun = imagine(word, current_adjectives.copy())
                            noun_dict[word] = new_noun
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 명사(목적어) 추출: '{word}' + '{next_word}'")
                        current_adjectives = []
                    i += 2  
                    continue
                
                elif next_pos == 'JKG':  # 소유격 조사('의')
                    # [추가] 소유격은 명사를 등록만 하고 역할 설정 안함
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 소유격 명사 등록: '{word}' + '{next_word}' (역할 설정 안함)")
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
                    
                    if next_word in ['에게', '에게서', '한테', '한테서']:
                        if word not in noun_dict:
                            new_noun = imagine(word)
                            noun_dict[word] = new_noun
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 간접목적어 추출: '{word}' + '{next_word}'")
                    else:
                        # [수정] 위치 정보는 이미 명사 단계에서 저장됨
                        # JKM 조사는 형용사나 기타 수식어로 처리
                        if DEBUG_MORPHS:
                            print(f"[DEBUG] 위치/방향 조사: '{next_word}' (위치 정보는 명사 단계에서 처리됨)")
                    # 관형형 형용사는 바로 앞 명사에서 소비됐으므로 다음
                    # 명사까지 넘어가 속성이 중복 저장되지 않게 비운다.
                    current_adjectives = []
                    i += 2  
                    continue
                
                elif next_pos.startswith('JK') :  # 주어
                    if word not in noun_dict:
                        new_noun = imagine(word, current_adjectives.copy())
                        noun_dict[word] = new_noun
                    else:
                        # [수정] 형용사 중복 제외하고 추가
                        for adj in current_adjectives:
                            if adj not in noun_dict[word].adjectives:
                                noun_dict[word].adjectives.append(adj)
                    current_subject = noun_dict[word]
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 명사(주어) 추출: '{word}' + '{next_word}'")
                    current_adjectives = []
                    i += 2  
                    continue

                # 조사가 있지만 주어/목적어 역할이 아닌 경우는 fallback 명사로만 저장
                elif next_pos.startswith('JK'):
                    noun_stack.append(noun_dict[word])
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] 역할 미확정 명사 fallback 저장: '{word}' (조사: {next_pos})")
            
            # [수정] 조사 없는 경우 또는 처리되지 않은 조사의 경우
            # 조사 없는 명사는 fallback 스택에 저장
            if not (i + 1 < len(tokens) and tokens[i + 1][1].startswith('JK')) and word not in positions and word in noun_dict:
                noun_stack.append(noun_dict[word])
                if DEBUG_MORPHS:
                    print(f"[DEBUG] 조사 없는 명사 fallback 저장: '{word}'")
            if DEBUG_MORPHS:
                print(f"[DEBUG] 명사 '{word}' 처리 완료 (조사: {tokens[i+1][1] if i+1 < len(tokens) else 'None'})")
            current_adjectives = []
        
        # [Level 1] 동사 처리 및 원형 복원
        elif pos in ['VV', 'VXV']:  
            # 동사 처리 시점의 fallback 명사 이력을 보존하고 스택을 비운다.
            if noun_stack:
                noun_stack_history.extend(noun_stack)
                if DEBUG_MORPHS:
                    print(f"[DEBUG] 동사 처리 전 noun_stack 반영: {[n.noun for n in noun_stack]}")
                noun_stack = []

            base_verb = get_base_form(word, pos)  # KKMA pos 전달
            all_verbs.append(base_verb)
            if DEBUG_MORPHS:
                print(f"[DEBUG] 동사 추출: '{word}' (pos: {pos}) -> 원형: '{base_verb}'")
        
        # 종결어미 처리
        elif pos.startswith('EF'):
            # [변경] 스택의 최상위 명사에 형용사 추가 (중복 제외)
            if current_adjectives and noun_stack:
                for adj in current_adjectives:
                    if adj not in noun_stack[-1].adjectives:
                        noun_stack[-1].adjectives.append(adj)
                current_adjectives = []
            noun_stack = []  # [변경] 문장 끝에 스택 리셋
            current_subject = None
            if DEBUG_MORPHS:
                print(f"[DEBUG] 종결어미 감지: '{word}' (스택 리셋)")
        
        i += 1
    
    # [변경] 함수 끝: 남아있는 형용사를 스택의 마지막 명사에 추가 (중복 제외)
    if current_adjectives and noun_stack:
        for adj in current_adjectives:
            if adj not in noun_stack[-1].adjectives:
                noun_stack[-1].adjectives.append(adj)

    adjective_bindings = assign_adjectives_to_nouns(
        tokens,
        noun_dict,
        default_subject=default_subject,
        focus_entity=focus_entity,
        default_object=default_object,
        default_experiencer=default_experiencer,
    )

    adjective_bindings = _attach_subjectless_adnominal_verbs_as_attributes(
        tokens,
        noun_dict,
        adjective_bindings,
        default_object=default_object,
        question_required_roles=question_required_roles,
        question_adnominal_patterns=question_adnominal_patterns,
    )

    # noun_stack 순서에 의존한 기존 결과를 조사/절 기반 결과로 교체한다.
    relations = generate_relations(
        tokens,
        default_subject,
        noun_dict,
        focus_entity=focus_entity,
        default_object=default_object,
        default_object_predicates=default_object_predicates,
        default_indirect_object=default_indirect_object,
        remembered_focus_entity=remembered_focus_entity,
        relation_history=relation_history,
        question_required_roles=question_required_roles,
        question_adnominal_patterns=question_adnominal_patterns,
    )

    adjective_bindings = _realign_adnominal_adjectives(
        tokens,
        noun_dict,
        relations,
        adjective_bindings,
    )

    adjective_bindings = _link_object_complements_to_direct_objects(
        noun_dict,
        relations,
        adjective_bindings,
    )

    adjective_bindings = _attach_quantity_targets(
        tokens,
        noun_dict,
        relations,
        adjective_bindings,
    )

    adjective_bindings = _cleanup_imagine_objects(
        sentence,
        tokens,
        noun_dict,
        relations,
        adjective_bindings,
    )
    
    if DEBUG_MORPHS:
        print(f"\n[DEBUG] ===== 추출 결과 =====")
        print(f"[DEBUG] noun_dict 키: {list(noun_dict.keys())}")
        print(f"[DEBUG] noun_stack_history: {[n.noun for n in noun_stack_history]}")
        print(f"[DEBUG] 추출된 형용사들: {all_adjectives}")
        print(f"[DEBUG] 형용사 연결: {adjective_bindings}")
        print(f"[DEBUG] 추출된 동사들: {all_verbs}")
        print(f"[DEBUG] 추출된 위치들: {all_positions}")
        print(f"[DEBUG] 생성된 관계: {relations}")
        print(f"[DEBUG] ===== 추출 완료 =====\n")
    
    if not noun_dict:
        question_reference_objects = [
            default_object,
            focus_entity,
            default_indirect_object,
            remembered_focus_entity,
            default_experiencer,
        ]
        created_entity_mentions = _record_entity_tree_mentions(
            entity_tree,
            [],
            question_entities=question_entities,
            question_reference_objects=question_reference_objects,
            turn_index=entity_turn_index,
        )
        entity_resolutions = entity_tree.resolve_turn_mentions(
            created_entity_mentions
        )
        return {
            'adjectives': all_adjectives,
            'verbs': all_verbs,
            'position': all_positions,
            'entity_tree': entity_tree,
            'entity_mentions': created_entity_mentions,
            'entity_resolutions': entity_resolutions,
            'identity_corrections': [],
        }
    
    # [단순화] noun_dict의 모든 명사를 그냥 반환 (순서: noun_dict의 순서)
    noun_objects = list(noun_dict.values())
    question_reference_objects = [
        default_object,
        focus_entity,
        default_indirect_object,
        remembered_focus_entity,
        default_experiencer,
    ]
    created_entity_mentions = _record_entity_tree_mentions(
        entity_tree,
        noun_objects,
        question_entities=question_entities,
        question_reference_objects=question_reference_objects,
        turn_index=entity_turn_index,
    )
    entity_resolutions = entity_tree.resolve_turn_mentions(
        created_entity_mentions
    )
    identity_corrections = _apply_entity_identity_to_relations(
        entity_tree,
        relations,
        created_entity_mentions,
        default_subject=default_subject,
    )
    # noun_stack에 들어갔던 명사를 순서 유지 + 중복 제거로 반환
    noun_stack_nouns = []
    seen_stack_nouns = set()
    for noun_obj in noun_stack_history:
        if (
            noun_obj.noun in noun_dict
            and noun_obj.noun not in seen_stack_nouns
        ):
            seen_stack_nouns.add(noun_obj.noun)
            noun_stack_nouns.append(noun_dict[noun_obj.noun])
    
    if DEBUG_MORPHS:
        print(f"[DEBUG] 반환하는 noun_objects: {[n.noun for n in noun_objects]}")
        print(f"[DEBUG] 반환하는 noun_stack_nouns: {[n.noun for n in noun_stack_nouns]}")
    
    return {
        'noun_objects': noun_objects,
        'relations': relations,
        'noun_stack_nouns': noun_stack_nouns,
        'adjective_bindings': adjective_bindings,
        'entity_tree': entity_tree,
        'entity_mentions': created_entity_mentions,
        'entity_resolutions': entity_resolutions,
        'identity_corrections': identity_corrections,
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

def extract_head_noun_only(text, tokens=None):
    """[개선] 주어(JKS 조사) 중심의 명사만 추출, 위치명사 제외
    
    - 조사가 JKS(이/가)인 명사만 추출
    - 위치명사인 경우 제외 (위치명사 앞의 명사도 함께 제외)
    - 다른 조사가 붙은 명사는 제외 (목적어, 간접목적어 등)
    """
    text = text.strip()
    if tokens is None:
        tokens = postprocess_kkma_pos(text)
    
    head_nouns = []
    skip_indices = set()  # 위치명사 앞의 명사 인덱스 기록
    
    # 먼저 위치명사를 찾아서 그 앞의 명사 인덱스 기록
    for i in range(len(tokens)):
        word, pos = tokens[i]
        if pos.startswith('N') and word in positions:
            # 앞의 명사 찾기
            if i > 0:
                prev_word, prev_pos = tokens[i - 1]
                if prev_pos.startswith('N'):
                    skip_indices.add(i - 1)
            if DEBUG_MORPHS:
                print(f"[DEBUG] extract_head_noun_only: '{word}' 위치명사 감지, 앞의 명사 제외")
    
    # 주어(JKS 조사) 추출
    for i in range(len(tokens) - 1):
        word, pos = tokens[i]
        next_word, next_pos = tokens[i + 1]
        
        # JKS(이/가) 조사를 가진 명사 = 주어
        if pos.startswith('N') and next_pos == 'JKS':
            # 위치명사는 제외
            if word not in positions and i not in skip_indices:
                head_nouns.append(word)
                if DEBUG_MORPHS:
                    print(f"[DEBUG] extract_head_noun_only: '{word}' (JKS) 추출")
        
        # 조사가 붙어있으면서 JKS가 아니면 제외 (목적어, 간접목적어 등)
        elif pos.startswith('N') and next_pos.startswith('JK'):
            if DEBUG_MORPHS:
                print(f"[DEBUG] extract_head_noun_only: '{word}' 제외 (조사: {next_pos} - 주어 아님)")
    
    return head_nouns

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

def run_test_case(json_file):
    """테스트 JSON 하나를 읽어 기존 분석·출력 흐름을 실행한다."""
    json_file = str(json_file)
    original_conversations = []
    summary_nouns = []
    summary_relations = []
    try:
        conversations = load_qa_from_json(json_file)
        original_conversations = [
            conversation.get("text", "") for conversation in conversations
        ]
        
        if not conversations[0].get('text', "").startswith("Q"):    
            print("=" * 60)
            print("[ 명사, 형용사, 동사 추출 결과 ]")
            print("=" * 60)
            entity_tree = EntityTree()
            entity_turn_index = 0
            
            for i, conversation in enumerate(conversations, 1):
                text = conversation.get('text', "")
                print(f"\n원문 [{i}]: {text}")
                
                split_result = split_sentences_by_connective(text)
                sentences = [s for s, _ in split_result]
                print(f"분리된 문장: {sentences}")
                
                print("\n[추출 결과]")
                for j, (sentence, tokens) in enumerate(split_result, 1):
                    entity_turn_index += 1
                    result = extract_nouns_adjectives_verbs(
                        sentence,
                        tokens,
                        entity_tree=entity_tree,
                        entity_turn_index=entity_turn_index,
                    )
                    print(f"  [{j}] {sentence}")
                    
                    # [Level 3] 위험어 감지 시 스킵 로직
                    if result.get("error") == "DANGER_WORD_DETECTED":
                        print("      [주의] 임상적 위험어가 포함되어 필터링 되었습니다.")
                        continue
                        
                    if 'noun_objects' in result: 
                            noun_objects = result['noun_objects']
                            relations = result['relations']
                            summary_nouns.extend(noun_objects)
                            summary_relations.extend(relations)
                            
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
            entity_tree = EntityTree()
            entity_turn_index = 1
            question_entities_for_answer = []
            remembered_focus_entity = imagine("나")
            remembered_object_entity = None
            remembered_object_predicates = None
            focus_for_answer = None
            object_for_answer = None
            object_predicates_for_answer = None
            indirect_for_answer = None
            experiencer_for_answer = None
            required_roles_for_answer = set()
            adnominal_patterns_for_answer = []
            initial_focus_entity = None
            current_question_focus_entity = None
            initial_object_entity = None
            initial_object_predicates = None
            initial_indirect_entity = None
            initial_experiencer_entity = None
            initial_required_roles = set()
            initial_adnominal_patterns = []

            seting_question_text = seting[0].get('text', "")
            if seting_question_text.startswith("Q:"):
                seting_question_tokens = postprocess_kkma_pos(
                    seting_question_text[2:].strip()
                )
                initial_required_roles = extract_question_required_roles(
                    seting_question_tokens
                )
                initial_adnominal_patterns = extract_question_adnominal_patterns(
                    seting_question_tokens
                )
                initial_focus_name, _ = extract_question_object_focus(
                    seting_question_tokens
                )
                if initial_focus_name is not None:
                    remembered_focus_entity = imagine(initial_focus_name)
                    current_question_focus_entity = remembered_focus_entity
                    question_entities_for_answer.append(initial_focus_name)
                if (
                    current_question_focus_entity is not None
                    or (
                        remembered_focus_entity.noun != "나"
                        and not question_prefers_speaker_subject(seting_question_tokens)
                        and not question_has_nonperson_center(seting_question_tokens)
                    )
                ):
                    initial_focus_entity = remembered_focus_entity
                initial_object_name = extract_question_default_object(
                    seting_question_tokens
                )
                if initial_object_name is not None:
                    initial_object_entity = imagine(initial_object_name)
                    if initial_object_name not in question_entities_for_answer:
                        question_entities_for_answer.append(initial_object_name)
                    initial_object_predicates = question_object_predicate_scope(
                        seting_question_tokens,
                        initial_object_name,
                    )
                    if (
                        _is_likely_person(initial_object_name)
                        and initial_object_predicates is not None
                    ):
                        remembered_object_entity = initial_object_entity
                        remembered_object_predicates = initial_object_predicates
                initial_indirect_name = extract_question_indirect_object(
                    seting_question_tokens
                )
                if initial_indirect_name is not None:
                    initial_indirect_entity = imagine(initial_indirect_name)
                    if initial_indirect_name not in question_entities_for_answer:
                        question_entities_for_answer.append(initial_indirect_name)
                elif current_question_focus_entity is not None:
                    initial_indirect_entity = current_question_focus_entity
                initial_experiencer_name = extract_question_default_experiencer(
                    seting_question_tokens
                )
                if initial_experiencer_name is not None:
                    initial_experiencer_entity = imagine(
                        initial_experiencer_name
                    )

            seting_text = seting[1].get('text', "")
            seting_text = seting_text[2:].strip()
            seting_tokens = postprocess_kkma_pos(seting_text)
            result = extract_nouns_adjectives_verbs(
                seting_text,
                seting_tokens,
                default_subject=imagine("나"),
                focus_entity=initial_focus_entity,
                default_object=initial_object_entity,
                default_object_predicates=initial_object_predicates,
                default_indirect_object=initial_indirect_entity,
                remembered_focus_entity=remembered_focus_entity,
                default_experiencer=initial_experiencer_entity,
                question_required_roles=initial_required_roles,
                question_adnominal_patterns=initial_adnominal_patterns,
                entity_tree=entity_tree,
                entity_turn_index=entity_turn_index,
                question_entities=question_entities_for_answer,
            )
            
            if 'noun_objects' in result:
                nouns = result['noun_objects']
            else:
                nouns = []
            
            print(f"설정 명사 객체: {nouns}")
            current_noun = None
            all_relations = list(result.get('relations', []))
            
            for i, conversation in enumerate(conversations, 1):
                text = conversation.get('text', "")
                if text.startswith("Q:"):
                    # [Q 처리] 주어를 찾는 로직
                    current_noun = None
                    question_entities_for_answer = []
                    text = text[2:].strip()
                    q_tokens = postprocess_kkma_pos(text)
                    required_roles_for_answer = extract_question_required_roles(
                        q_tokens
                    )
                    adnominal_patterns_for_answer = (
                        extract_question_adnominal_patterns(q_tokens)
                    )

                    question_focus_name, clears_focus = extract_question_object_focus(
                        q_tokens
                    )
                    if question_focus_name is not None:
                        remembered_focus_entity = imagine(question_focus_name)
                        current_question_focus_entity = remembered_focus_entity
                        question_entities_for_answer.append(question_focus_name)
                    else:
                        current_question_focus_entity = None

                    if current_question_focus_entity is not None:
                        focus_for_answer = current_question_focus_entity
                    elif (
                        question_prefers_speaker_subject(q_tokens)
                        or clears_focus
                        or question_has_nonperson_center(q_tokens)
                    ):
                        focus_for_answer = None
                    else:
                        # 이전 질문에서 기억한 인물은 수혜자·담화 문맥에는
                        # 남겨 두되, 다음 Q-A의 생략 주어로 자동 승격하지 않는다.
                        # 강한 주어 후보는 현재 질문에서 다시 확인된 인물뿐이다.
                        focus_for_answer = None
                    question_object_name = extract_question_default_object(q_tokens)
                    if question_object_name is not None:
                        object_for_answer = imagine(question_object_name)
                        if question_object_name not in question_entities_for_answer:
                            question_entities_for_answer.append(question_object_name)
                        object_predicates_for_answer = question_object_predicate_scope(
                            q_tokens,
                            question_object_name,
                        )
                        # 장기 기억은 사람 목적어이면서 적용 동사 범위가
                        # 확인된 경우만 갱신한다. 첫인상·편지 같은 현재 Q의
                        # 비인물 목적어가 배우자 기억을 덮어쓰지 않게 한다.
                        if (
                            _is_likely_person(question_object_name)
                            and object_predicates_for_answer is not None
                        ):
                            remembered_object_entity = object_for_answer
                            remembered_object_predicates = (
                                object_predicates_for_answer
                            )
                    else:
                        # 질문에 직접목적어 중심어가 새로 없으면 이전에 역할까지
                        # 확인한 목적어를 유지한다. 단어만 기억해 주어로 올리지 않는다.
                        object_for_answer = remembered_object_entity
                        object_predicates_for_answer = (
                            remembered_object_predicates
                        )
                    question_indirect_name = extract_question_indirect_object(
                        q_tokens
                    )
                    indirect_for_answer = (
                        imagine(question_indirect_name)
                        if question_indirect_name is not None
                        else current_question_focus_entity
                    )
                    if (
                        question_indirect_name is not None
                        and question_indirect_name not in question_entities_for_answer
                    ):
                        question_entities_for_answer.append(question_indirect_name)
                    question_experiencer_name = (
                        extract_question_default_experiencer(q_tokens)
                    )
                    experiencer_for_answer = (
                        imagine(question_experiencer_name)
                        if question_experiencer_name is not None
                        else None
                    )
                    
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
                    
                    # 주어를 찾지 못한 경우 JKS 조사를 가진 주어 명사 우선 추출
                    if current_noun is None:
                        extracted_items = extract_head_noun_only(text, q_tokens)
                        for noun_str in extracted_items:
                            for noun_obj in nouns:
                                if noun_str == noun_obj.noun:
                                    current_noun = noun_obj
                                    if DEBUG_MORPHS:
                                        print(f"[DEBUG] Q에서 주어 재추출: '{noun_str}' (JKS 조사 기반)")
                                    break
                            if current_noun is not None:
                                break
                    
                    if current_noun is not None:
                        print(f"\n[Q {i}] {text}")
                        print(f"  → 선택된 주어: {current_noun.noun}")
                
                elif text.startswith("A:"):
                    # [A 처리] 한 줄 분석: extract_nouns_adjectives_verbs 사용
                    # 화자와 Q에서 이어받은 대화 중심 대상을 분리해서 전달
                    text = text[2:].strip()
                    answer_tokens = postprocess_kkma_pos(text)
                    default_subject_for_a = imagine("나")
                    entity_turn_index += 1
                    
                    # [디버그] 문장 분리 확인
                    if DEBUG_MORPHS:
                        print(f"[DEBUG] === A 처리 시작 ===")
                        print(f"[DEBUG] 원문: {text}")
                        print(f"[DEBUG] 토큰: {answer_tokens}")
                        print(f"[DEBUG] A 기본 주어: '{default_subject_for_a.noun}'")
                        print(
                            f"[DEBUG] A 대화 중심 대상: "
                            f"'{remembered_focus_entity.noun}' "
                            f"(주어 적용: "
                            f"{focus_for_answer.noun if focus_for_answer else '나'})"
                        )
                     
                    result = extract_nouns_adjectives_verbs(
                        text,
                        answer_tokens,
                        default_subject=default_subject_for_a,
                        focus_entity=focus_for_answer,
                        default_object=object_for_answer,
                        default_object_predicates=object_predicates_for_answer,
                        default_indirect_object=indirect_for_answer,
                        remembered_focus_entity=remembered_focus_entity,
                        default_experiencer=experiencer_for_answer,
                        relation_history=all_relations,
                        question_required_roles=required_roles_for_answer,
                        question_adnominal_patterns=adnominal_patterns_for_answer,
                        entity_tree=entity_tree,
                        entity_turn_index=entity_turn_index,
                        question_entities=question_entities_for_answer,
                    )
                    
                    if result.get("error") == "DANGER_WORD_DETECTED":
                        print(f"[A {i}] {text}")
                        print("  [주의] 임상적 위험어 필터링됨")
                        continue

                    print(f"[A {i}] {text}")
                    
                    if 'noun_objects' in result: 
                            # 관계와 형용사 모두 추출
                            extract_nouns_list = result['noun_objects']
                            relations = result.get('relations', [])
                            noun_stack_nouns = result.get('noun_stack_nouns', [])
                            
                            # [개선] A에서 추출된 명사들을 필터링하여 nouns 리스트에 추가
                            # 일반 명사는 JKS 중심으로 유지하되, 형용사가 연결된 명사는
                            # 생략 주어나 관형형 대상일 수 있으므로 반드시 누적한다.
                            head_nouns_in_answer = extract_head_noun_only(text, answer_tokens)
                            
                            for noun_obj in extract_nouns_list:
                                has_adjective = bool(noun_obj.adjectives)
                                has_relative_clause = bool(
                                    noun_obj.relative_clauses
                                )
                                if (
                                    noun_obj.noun not in head_nouns_in_answer
                                    and not has_adjective
                                    and not has_relative_clause
                                ):
                                    if DEBUG_MORPHS:
                                        print(f"[DEBUG] 명사 제외: '{noun_obj.noun}' (JKS 조사 없음 또는 위치명사)")
                                    continue
                                
                                existing_noun = None
                                for n in nouns:
                                    if n.noun == noun_obj.noun:
                                        existing_noun = n
                                        break
                                
                                if existing_noun is None:
                                    # 새로운 명사이면 추가
                                    nouns.append(noun_obj)
                                    if DEBUG_MORPHS:
                                        if has_adjective:
                                            reason = "형용사 연결"
                                        elif has_relative_clause:
                                            reason = "관형절 연결"
                                        else:
                                            reason = "JKS 기반"
                                        print(f"[DEBUG] 새로운 명사 추가: '{noun_obj.noun}' ({reason})")
                                else:
                                    # 기존 명사면 형용사 병합 (중복 제외)
                                    for adj in noun_obj.adjectives:
                                        if adj not in existing_noun.adjectives:
                                            existing_noun.adjectives.append(adj)
                                            if DEBUG_MORPHS:
                                                print(f"[DEBUG] 형용사 추가: '{existing_noun.noun}'에 '{adj}' 추가")
                                    for relative_clause in noun_obj.relative_clauses:
                                        existing_noun.add_relative_clause(
                                            relative_clause
                                        )

                            # [추가] 마지막에 noun stack 이력 명사 전부 반영
                            for noun_obj in noun_stack_nouns:
                                existing_noun = None
                                for n in nouns:
                                    if n.noun == noun_obj.noun:
                                        existing_noun = n
                                        break

                                if existing_noun is None:
                                    nouns.append(noun_obj)
                                    if DEBUG_MORPHS:
                                        print(f"[DEBUG] noun_stack 명사 추가: '{noun_obj.noun}'")
                                else:
                                    for adj in noun_obj.adjectives:
                                        if adj not in existing_noun.adjectives:
                                            existing_noun.adjectives.append(adj)
                                    for relative_clause in noun_obj.relative_clauses:
                                        existing_noun.add_relative_clause(
                                            relative_clause
                                        )
                                    for pos_info in noun_obj.position:
                                        if pos_info not in existing_noun.position:
                                            existing_noun.position.append(pos_info)
                            
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
            summary_nouns = list(nouns)
            summary_relations = list(all_relations)

        return {
            "path": str(Path(json_file).resolve()),
            "original": original_conversations,
            "imagine_objects": summary_nouns,
            "relations": summary_relations,
        }
    except FileNotFoundError:
        print(f"{json_file} 파일이 없어 실행을 종료합니다.")
        return None


def _display_value(value):
    """Imagine 필드의 객체 참조를 사람이 읽기 좋은 이름으로 표시한다."""
    if isinstance(value, imagine):
        return value.noun
    if isinstance(value, (list, tuple, set)):
        return [_display_value(item) for item in value]
    return value


def print_test_case_summary(summary):
    """원문, Imagine, Relation 순서로 테스트 결과를 간결하게 출력한다."""
    print("\n[원문]")
    original = summary["original"]
    if original and all(
        text.startswith(("Q:", "A:")) for text in original
    ):
        for turn_index in range(0, len(original), 2):
            print(f"\n[{turn_index // 2 + 1}턴]")
            print(original[turn_index])
            if turn_index + 1 < len(original):
                print(original[turn_index + 1])
    else:
        for text_index, text in enumerate(original, 1):
            print(f"[{text_index}] {text}")

    print("\n[최종 Imagine 객체]")
    imagine_objects = summary["imagine_objects"]
    if not imagine_objects:
        print("(생성된 Imagine 객체가 없습니다)")
    for object_index, noun_object in enumerate(imagine_objects, 1):
        print(f"[{object_index}] {noun_object.noun}")
        if noun_object.adjectives:
            print(f"    adjectives: {noun_object.adjectives}")
        if noun_object.position:
            print(f"    position: {_display_value(noun_object.position)}")
        if noun_object.moderators:
            print(f"    moderators: {_display_value(noun_object.moderators)}")
        if noun_object.target is not None:
            print(f"    target: {_display_value(noun_object.target)}")
        if noun_object.relative_clauses:
            print(f"    relative_clauses: {noun_object.relative_clauses}")

    print("\n[최종 Relation 객체]")
    relations = summary["relations"]
    if not relations:
        print("(생성된 Relation 객체가 없습니다)")
    for relation_index, relation in enumerate(relations, 1):
        print(f"[{relation_index}] {relation}")


def _test_case_sort_key(path):
    """case10이 case2보다 먼저 정렬되지 않도록 번호 기준으로 정렬한다."""
    match = re.match(r"case(\d+)", path.name, re.IGNORECASE)
    return (int(match.group(1)) if match else float("inf"), path.name)


def _resolve_test_case_argument(argument, test_case_dir):
    """`2`, 파일명, 상대 경로, 절대 경로를 테스트 JSON 경로로 바꾼다."""
    if argument.isdigit():
        matches = sorted(
            test_case_dir.glob(f"case{int(argument)}_*_20turn.json"),
            key=_test_case_sort_key,
        )
        if matches:
            return matches[0]
        raise FileNotFoundError(f"case{argument} 테스트 파일을 찾을 수 없습니다.")

    requested = Path(argument)
    candidates = (
        requested,
        Path.cwd() / requested,
        test_case_dir / requested,
    )
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() == ".json":
            return candidate.resolve()
    raise FileNotFoundError(f"테스트 파일을 찾을 수 없습니다: {argument}")


def main():
    """VS Code에서 case1~10 테스트 JSON만 직접 실행한다.

    인자가 없으면 outputs 폴더의 20턴 테스트를 전부 실행한다.
    예: `python prompt_maker_succeed_3_span_experiment.py 3`
        `python prompt_maker_succeed_3_span_experiment.py case3_market_20turn.json`
    형태소 디버그 출력이 필요하면 마지막에 `--debug`를 붙인다.
    """
    global DEBUG_MORPHS

    raw_arguments = sys.argv[1:]
    DEBUG_MORPHS = "--debug" in raw_arguments
    requested_cases = [arg for arg in raw_arguments if arg != "--debug"]
    test_case_dir = Path(__file__).resolve().parent

    try:
        if requested_cases:
            test_case_files = [
                _resolve_test_case_argument(argument, test_case_dir)
                for argument in requested_cases
            ]
        else:
            test_case_files = sorted(
                test_case_dir.glob("case*_20turn.json"),
                key=_test_case_sort_key,
            )
    except FileNotFoundError as error:
        print(f"[오류] {error}")
        return 1

    if not test_case_files:
        print(f"[오류] 테스트 케이스가 없습니다: {test_case_dir}")
        return 1

    print("=" * 60)
    print("succeed3 테스트 전용 실행")
    print(f"테스트 수: {len(test_case_files)}")
    print(f"형태소 디버그: {'사용' if DEBUG_MORPHS else '미사용'}")
    print("=" * 60)

    for index, test_case_file in enumerate(test_case_files, 1):
        print("\n" + "#" * 60)
        print(f"[{index}/{len(test_case_files)}] {test_case_file.name}")
        print("#" * 60)
        if DEBUG_MORPHS:
            summary = run_test_case(test_case_file)
        else:
            # 기본 실행에서는 기존 중간 출력은 숨기고 최종 객체만 정리한다.
            with contextlib.redirect_stdout(io.StringIO()):
                summary = run_test_case(test_case_file)
        if summary is not None:
            print_test_case_summary(summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
