from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, JSON, Date, Time
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.database import Base

# 1. User Domain (사용자)
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    nickname = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 관계 설정
    trips = relationship("Trip", back_populates="user", cascade="all, delete-orphan")
    analysis_logs = relationship("AnalysisLog", back_populates="user")
    preference = relationship("UserPreference", back_populates="user", uselist=False, cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")

# 2. Place Domain (장소/관광지 데이터)
class Place(Base):
    __tablename__ = "places"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)  # 장소명
    category = Column(String, nullable=True)           # 관광지, 식당, 카페 등
    address = Column(String, nullable=True)            # 전체 주소
    
    # 위치 정보 (기능 6.1 경로 계산용)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    # 상세 정보 (기능 7)
    description = Column(Text, nullable=True)          # 간단 설명
    tags = Column(JSON, nullable=True)                 # 분위기 태그 (자연, 도심, 야경 등) - 검색용
    image_url = Column(String, nullable=True)          # 대표 이미지 URL

    # 운영 정보 (기능 7.2)
    operating_hours = Column(Text, nullable=True)      # "09:00 - 18:00" (텍스트로 유연하게 저장)
    closed_days = Column(String, nullable=True)        # "매주 월요일"
    fee_info = Column(String, nullable=True)           # "성인 5000원"

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_festival = Column(Boolean, default=False)
    event_start_date = Column(String, nullable=True) # YYYYMMDD
    event_end_date = Column(String, nullable=True)   # YYYYMMDD

# 3. Photo Analysis Domain (사진 분석 & 로그)
class AnalysisLog(Base):
    __tablename__ = "analysis_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    # 이미지 파일 경로 (S3 url 혹은 로컬 경로)
    image_path = Column(String, nullable=False)
    
    # 분석 결과 (기능 2.1, 2.2)
    predicted_location_name = Column(String, nullable=True) # AI가 추측한 장소명
    confidence_score = Column(Float, nullable=True)         # 신뢰도 점수
    atmosphere_tags = Column(JSON, nullable=True)           # ["바다", "노을", "휴양지"]
    
    # 분석 결과 타입 (기능 2.3) - 'A', 'B', 'C'
    result_type = Column(String(10), nullable=True) 

    # 사용자 선택 결과 (기능 3.1, 4.3)
    # 분석 후 사용자가 최종적으로 선택한 장소와 매핑 (Type A, B 과정을 통해 확정된 곳)
    selected_place_id = Column(Integer, ForeignKey("places.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 관계 설정
    user = relationship("User", back_populates="analysis_logs")
    selected_place = relationship("Place")


# 4. Trip & Itinerary Domain (여행 일정)
class Trip(Base):
    __tablename__ = "trips"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    title = Column(String, nullable=False)        # 예: "부산 힐링 여행"
    start_date = Column(Date, nullable=False)     # 여행 시작일
    end_date = Column(Date, nullable=False)       # 여행 종료일

    # 여행 조건 요약 (기능 5.1 저장용 - AI 재설계시 참고)
    conditions = Column(JSON, nullable=True)      # {"max_places_per_day": 3, "start_location": "서울역"}

    # 신규 필드
    region = Column(String, nullable=True)                    # "부산", "제주" 등 지역
    generation_method = Column(String, default="manual")      # "ai" 또는 "manual"
    preference_snapshot = Column(JSON, nullable=True)         # 생성 시점 선호도 스냅샷

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 관계 설정
    user = relationship("User", back_populates="trips")
    itineraries = relationship("Itinerary", back_populates="trip", cascade="all, delete-orphan")


class Itinerary(Base):
    """
    개별 일정 (Trip 상세)
    어느 날짜(day_n)에 몇 번째 순서(step)로 어디(place_id)를 가는지
    """
    __tablename__ = "itineraries"

    id = Column(Integer, primary_key=True, index=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=False)
    place_id = Column(Integer, ForeignKey("places.id"), nullable=False)
    
    day_number = Column(Integer, nullable=False)  # 1일차, 2일차... (날짜 계산 편의를 위해)
    order_index = Column(Integer, nullable=False) # 방문 순서 (1, 2, 3...)
    
    # 이동 및 체류 정보 (기능 5.3, 6.2)
    arrival_time = Column(Time, nullable=True)    # 예상 도착 시간
    stay_duration = Column(Integer, nullable=True)# 체류 시간 (분 단위)
    memo = Column(Text, nullable=True)            # 사용자 메모

    # 신규 필드
    travel_time_from_prev = Column(Integer, nullable=True)  # 이전 장소로부터 이동시간(분)
    transport_mode = Column(String, nullable=True)          # "walk", "car", "public"

    # 관계 설정
    trip = relationship("Trip", back_populates="itineraries")
    place = relationship("Place")


# 5. UserPreference Domain (사용자 선호도)
class UserPreference(Base):
    """사용자 여행 선호도 설정"""
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # 카테고리 선호도 (가중치)
    category_weights = Column(JSON, nullable=True)    # {"관광지": 0.8, "카페": 0.5, "맛집": 0.7}

    # 선호 테마/분위기
    preferred_themes = Column(JSON, nullable=True)    # ["힐링", "액티비티", "역사"]

    # 여행 스타일
    travel_pace = Column(String, nullable=True)       # "relaxed", "moderate", "packed"
    budget_level = Column(String, nullable=True)      # "low", "medium", "high"

    # 시간 선호
    preferred_start_time = Column(Time, nullable=True)  # 하루 시작 시간 (기본 09:00)
    preferred_end_time = Column(Time, nullable=True)    # 하루 종료 시간 (기본 21:00)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 관계 설정
    user = relationship("User", back_populates="preference")


# 6. ChatSession Domain (대화 세션)
class ChatSession(Base):
    """대화형 일정 수정을 위한 채팅 세션"""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=True)

    # 대화 히스토리 (GPT 컨텍스트 유지용)
    messages = Column(JSON, nullable=True)            # [{"role": "user/assistant", "content": "..."}]

    # 현재 작업 상태
    current_state = Column(String, nullable=True)     # "gathering", "generating", "modifying"

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 관계 설정
    user = relationship("User", back_populates="chat_sessions")
    trip = relationship("Trip")