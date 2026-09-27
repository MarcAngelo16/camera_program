TARGET ?= x64
SDK_ROOT ?= ../..
MVSDK_INCLUDE ?= $(SDK_ROOT)/include
MVSDK_LIB_DIR ?= $(SDK_ROOT)/lib/linux/$(TARGET)
CROSS_COMPILE ?=
PKG_CONFIG ?= pkg-config
CXX ?= $(CROSS_COMPILE)g++

APP := GTK_Demo
SRCS := GTK_demo.cpp $(wildcard src/*.cpp)
OBJS := $(SRCS:.cpp=.o)

GTK_CFLAGS := $(shell $(PKG_CONFIG) --cflags gtk+-2.0 gthread-2.0 2>/dev/null)
GTK_LIBS := $(shell $(PKG_CONFIG) --libs gtk+-2.0 gthread-2.0 2>/dev/null)
CXXFLAGS += -O2 -Wall -I$(MVSDK_INCLUDE) $(GTK_CFLAGS) -Iinc
LDFLAGS += -L$(MVSDK_LIB_DIR)
LDLIBS += -lMVSDK $(GTK_LIBS) -lpthread -lrt

.PHONY: all clean

all: $(APP)

$(APP): $(OBJS)
	$(CXX) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<

clean:
	rm -f $(APP) $(OBJS)
