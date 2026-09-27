TARGET ?= x64
SDK_ROOT ?= ../..

BUILDABLE_DEMOS := GTK_Demo multicamera_demo Sampli_Save_Demo OpenCv

.PHONY: all clean $(BUILDABLE_DEMOS)

all: $(BUILDABLE_DEMOS)

$(BUILDABLE_DEMOS):
	$(MAKE) -C $@ TARGET=$(TARGET) SDK_ROOT=$(abspath $(SDK_ROOT))

clean:
	@for demo in $(BUILDABLE_DEMOS); do \
		$(MAKE) -C $$demo clean; \
	done
