TARGET ?= x64
SDK_ROOT ?= ../..
MVSDK_INCLUDE ?= $(SDK_ROOT)/include
MVSDK_LIB_DIR ?= $(SDK_ROOT)/lib/linux/$(TARGET)
CROSS_COMPILE ?=
CXX ?= $(CROSS_COMPILE)g++

APP := Sampli_Save_Demo
SRCS := main.cpp
OBJS := $(SRCS:.cpp=.o)

CXXFLAGS += -O2 -Wall -I$(MVSDK_INCLUDE)
LDFLAGS += -L$(MVSDK_LIB_DIR)
LDLIBS += -lMVSDK -lpthread -lrt

.PHONY: all clean

all: $(APP)

$(APP): $(OBJS)
	$(CXX) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<

clean:
	rm -f $(APP) $(OBJS)
