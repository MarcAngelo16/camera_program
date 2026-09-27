TARGET ?= x64
SDK_ROOT ?= ../..
MVSDK_INCLUDE ?= $(SDK_ROOT)/include
MVSDK_LIB_DIR ?= $(SDK_ROOT)/lib/linux/$(TARGET)
CROSS_COMPILE ?=
PKG_CONFIG ?= pkg-config
CXX ?= $(CROSS_COMPILE)g++

APP := OpenCv
SRCS := main.cpp
OBJS := $(SRCS:.cpp=.o)

OPENCV_CFLAGS := $(shell $(PKG_CONFIG) --cflags opencv 2>/dev/null || $(PKG_CONFIG) --cflags opencv4 2>/dev/null)
OPENCV_LIBS := $(shell $(PKG_CONFIG) --libs opencv 2>/dev/null || $(PKG_CONFIG) --libs opencv4 2>/dev/null)
CXXFLAGS += -O2 -Wall -I$(MVSDK_INCLUDE) $(OPENCV_CFLAGS)
LDFLAGS += -L$(MVSDK_LIB_DIR)
LDLIBS += -lMVSDK $(OPENCV_LIBS) -lpthread -lrt

.PHONY: all clean

all: $(APP)

$(APP): $(OBJS)
	$(CXX) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<

clean:
	rm -f $(APP) $(OBJS)
